"""The registry-sourced reconcile path: plan a mirror from upstream tags, then apply it.

Split out of `reconcile.py` so that file can be a driver over source classes rather than
one path plus a filter. The functions below are the image path exactly as it was — nothing
in them changed in the move, and any correction to one belongs in its own commit. The
`RegistryPlanner` at the end of the file is the later addition that puts them behind
`PolicyPlanner`; its method bodies are those same loops, lifted.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from knock.config import (
    CACertSource,
    PackageMirror,
    RegistryConfig,
    match_registry_by_host,
    resolve_ca_certs,
    resolve_mirror,
    resolve_registry,
)
from knock.domain.attestation import COSIGN_ATTESTATION_ARTIFACT_TYPE, build_transform_statement
from knock.domain.collision import AliasTarget
from knock.domain.deletion_mode import DeletionMode, resolve_deletion_mode
from knock.domain.expand import ExpandedImport, VariantPlan, expand_import
from knock.domain.lifecycle import (
    PENDING_DELETION_ARTIFACT_TYPE,
    build_pending_deletion_annotations,
    parse_pending_mark,
)
from knock.domain.mirror_policy import Archive, MirrorPolicy, RegistrySource, TransformStep
from knock.domain.policy_merge import resolve_imports
from knock.domain.reconcile import (
    MirrorArtifact,
    SourceArtifact,
    reconcile_import,
)
from knock.domain.retention import resolve_archive
from knock.domain.sbom import build_sbom_annotations, build_sbom_statement, media_type_for
from knock.domain.scan.refs import is_referrers_fallback_tag
from knock.domain.stamp import build_stamp_annotations
from knock.domain.transforms.base import ResolvedResource, ResolvedStep, ResourceRef
from knock.domain.transforms.registry import DEFAULT_REGISTRY
from knock.domain.transforms.render import render, transform_version, validate_transform_steps
from knock.errors import ConfigError, InternalError, exit_code_for
from knock.ports.attestor import AttestorPort
from knock.ports.image_builder import BuildRequest, ImageBuilderPort
from knock.ports.registry import ImageInfo, Referrer, RegistryPort
from knock.ports.reporter import Counts, ErrorInfo, OperationEvent, OperationKind, Reporter
from knock.ports.sbom import SbomGeneratorPort
from knock.use_cases.registry_session import ensure_registry_session
from knock.use_cases.report import (
    Operation,
    PolicyReport,
    TargetReport,
    VariantReport,
    counts_of,
    merge_counts,
    node_status,
)

BASE_DIGEST_KEY = "org.opencontainers.image.base.digest"
CREATED_KEY = "org.opencontainers.image.created"
_REVISION_KEY = "org.opencontainers.image.revision"


def _parse_created(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_source_artifact(info: ImageInfo, *, now: datetime) -> SourceArtifact:
    # Unknown created time → use `now` (conservative: treated as just-pushed, so the
    # 7-day stability window skips an update rather than churning on unknown freshness).
    revision = info.annotations.get(_REVISION_KEY) or info.config_labels.get(_REVISION_KEY)
    return SourceArtifact(digest=info.digest, pushed_at=info.created or now, revision=revision)


def to_mirror_artifact(
    info: ImageInfo,
    *,
    transform_version_key: str | None = None,
    attested: bool = True,
    sbom_covered: bool = True,
) -> MirrorArtifact | None:
    base = info.annotations.get(BASE_DIGEST_KEY)
    if base is None:
        return None
    tv = info.annotations.get(transform_version_key) if transform_version_key else None
    return MirrorArtifact(
        base_digest=base,
        transform_version=tv,
        imported_at=_parse_created(info.annotations.get(CREATED_KEY)),
        attested=attested,
        sbom_covered=sbom_covered,
    )


def _read_cert_file(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError as e:
        raise ConfigError(f"cannot read CA cert file {path!r}: {e}") from e


@dataclass(frozen=True)
class _ResolvedTransform:
    resolved_steps: list[ResolvedStep]
    version: str


def _resolve_ref(
    ref: ResourceRef,
    ca_certs: dict[str, CACertSource],
    package_mirrors: dict[str, PackageMirror],
) -> ResolvedResource:
    if ref.kind == "caCert":
        ((name, src),) = resolve_ca_certs([ref.name], ca_certs)
        if src.pem is not None:
            content = src.pem
        else:
            # path is guaranteed non-None when pem is None by CACertSource._exactly_one
            assert src.path is not None
            content = _read_cert_file(src.path)
        return ResolvedResource(kind="caCert", name=name, filename=f"{name}.crt", content=content)
    if ref.kind == "packageMirror":
        m = resolve_mirror(ref.name, package_mirrors)
        return ResolvedResource(kind="packageMirror", name=ref.name, apt=m.apt, apk=m.apk)
    raise InternalError(f"no resolver for resource kind {ref.kind!r}")


def _resolve_transform(
    steps: list[TransformStep],
    ca_certs: dict[str, CACertSource],
    package_mirrors: dict[str, PackageMirror],
) -> _ResolvedTransform:
    resolved_steps: list[ResolvedStep] = []
    for step in steps:
        compiler = DEFAULT_REGISTRY.get(step.name)
        params = compiler.params_model.model_validate(step.params)
        resources = tuple(
            _resolve_ref(ref, ca_certs, package_mirrors) for ref in compiler.resource_refs(params)
        )
        resolved_steps.append(ResolvedStep(step=step, resources=resources))
    version = transform_version(resolved_steps)
    return _ResolvedTransform(resolved_steps=resolved_steps, version=version)


def _build_variant(
    *,
    builder: ImageBuilderPort,
    source_ref: str,
    dest_ref: str,
    resolved: _ResolvedTransform,
    platform: str,
    work_dir: Path | None = None,
    provenance: bool = False,
    tls_verify: bool = True,
) -> None:
    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="knock-build-", dir=work_dir) as tmp:
        ctx = Path(tmp)
        rendered = render(resolved.resolved_steps, source_ref=source_ref)
        for cf in rendered.context_files:
            (ctx / cf.path).write_text(cf.content)
        df_path = ctx / "Dockerfile"
        df_path.write_text(rendered.dockerfile)
        builder.build_and_push(
            BuildRequest(
                dockerfile_path=df_path,
                context_dir=ctx,
                image_ref=dest_ref,
                platform=platform,
                provenance=provenance,
                tls_verify=tls_verify,
            )
        )


@dataclass(frozen=True)
class _Plan:
    policy: MirrorPolicy
    expanded: ExpandedImport
    dest_repo: str
    config: RegistryConfig
    transforms: dict[str, _ResolvedTransform]  # variant name → resolved transform


def _require_registry_source(policy: MirrorPolicy) -> RegistrySource:
    """Narrow `policy.spec.source` to a `RegistrySource`.

    This module only reconciles registry-sourced policies: image/helmChart are always
    registry-sourced, and generic may be either (see mirror_policy.py's asymmetric
    source/artifactType rule). A git-sourced policy — skill, and any git-sourced
    generic — never reaches this function: the driver partitions its worklist by each
    planner's `handles` before the plan phase starts, and `RegistryPlanner.handles`
    claims registry sources only. If a git source does reach here, that is a bug in
    that partition, not a user-input problem: hence `InternalError`.
    """
    s = policy.spec.source
    if not isinstance(s, RegistrySource):
        raise InternalError(
            f"policy '{policy.metadata.name}' has a git source; "
            "reconcile only supports registry sources"
        )
    return s


def _source_repo(policy: MirrorPolicy) -> str:
    s = _require_registry_source(policy)
    return f"{s.registry}/{s.repository}"


def _run_stage[T](
    items: list[T], fn: Callable[[T], Operation], *, executor: ThreadPoolExecutor | None
) -> list[Operation]:
    """Run `fn` over `items`. Results preserve input order regardless of completion
    order (so the assembled report is deterministic). Sequential when executor is None;
    the `.result()` join is also the barrier that ends the stage."""
    if executor is None:
        return [fn(it) for it in items]
    futures = [executor.submit(fn, it) for it in items]
    return [f.result() for f in futures]


@dataclass(frozen=True)
class _ImportWork:
    variant: str
    vplan: VariantPlan
    out_tag: str
    src_tag: str
    kind: OperationKind


@dataclass(frozen=True)
class _SignWork:
    variant: str
    vplan: VariantPlan
    out_tag: str
    src_tag: str


@dataclass(frozen=True)
class _SbomWork:
    variant: str
    vplan: VariantPlan
    out_tag: str
    src_tag: str
    formats: list[str]


@dataclass(frozen=True)
class _AliasWork:
    variant: str
    alias: str
    target: str


@dataclass(frozen=True)
class _DeleteWork:
    out_tag: str
    reason: str = "dropped-from-selection"


def _apply_plan(
    plan: _Plan,
    *,
    registry: RegistryPort,
    builder: ImageBuilderPort,
    label_prefix: str,
    build_platform: str,
    work_dir: Path | None,
    now: datetime,
    dry_run_tags: bool,
    dry_run_deletions: bool,
    deletion_mode: DeletionMode,
    reporter: Reporter,
    policy_name: str,
    executor: ThreadPoolExecutor | None,
    attestor: AttestorPort | None,
    attest_builder_id: str,
    sbom_generator: SbomGeneratorPort | None,
    sbom_formats: list[str],
    retention_global: Archive | None = None,
) -> TargetReport:
    registry_source = _require_registry_source(plan.policy)
    src_repo = f"{registry_source.registry}/{registry_source.repository}"
    selected = sorted({tag for v in plan.expanded.variants for tag in v.tags})
    source: dict[str, SourceArtifact] = {
        tag: to_source_artifact(registry.inspect(f"{src_repo}:{tag}"), now=now) for tag in selected
    }
    tv_key = f"{label_prefix}.transform.version" if label_prefix else None
    transform_versions: dict[str, str | None] = {
        name: rt.version for name, rt in plan.transforms.items()
    }
    mirror: dict[str, MirrorArtifact] = {}
    mirror_digests: dict[str, str] = {}
    missing_sbom: dict[str, list[str]] = {}  # out_tag → configured formats whose referrer is absent
    # One unfiltered referrer probe per existing tag yields every artifactType present, feeding
    # BOTH coverage signals (signature + SBOM). Skipped when neither signing nor SBOM is
    # configured — nothing would route to a backfill stage (one fewer registry read per tag).
    need_probe = attestor is not None or bool(sbom_formats)
    # Registries without the referrers API store referrer manifests under a
    # `sha256-<digest>` tag in the SUBJECT's repo, so knock's own referrers surface in this
    # listing. They are not images (`inspect` fails on them) and are never mirror state.
    for out_tag in registry.list_tags(plan.dest_repo):
        if is_referrers_fallback_tag(out_tag):
            continue
        info = registry.inspect(f"{plan.dest_repo}:{out_tag}")
        present = (
            {r.artifact_type for r in registry.list_referrers(f"{plan.dest_repo}:{out_tag}")}
            if need_probe
            else set()
        )
        attested = attestor is None or COSIGN_ATTESTATION_ARTIFACT_TYPE in present
        absent = [fmt for fmt in sbom_formats if media_type_for(fmt) not in present]
        ma = to_mirror_artifact(
            info, transform_version_key=tv_key, attested=attested, sbom_covered=not absent
        )
        if ma is not None:
            mirror[out_tag] = ma
            mirror_digests[out_tag] = info.digest
            if absent:
                missing_sbom[out_tag] = absent

    marked_referrers: dict[str, list[Referrer]] = {}
    for out_tag in mirror:
        refs = registry.list_referrers(
            f"{plan.dest_repo}:{out_tag}", PENDING_DELETION_ARTIFACT_TYPE
        )
        if refs:
            marked_referrers[out_tag] = refs

    marked_selection: set[str] = set()
    marked_retention: set[str] = set()
    for out_tag, refs in marked_referrers.items():
        for ref in refs:
            reason = parse_pending_mark(label_prefix, out_tag, ref.annotations).reason
            (marked_retention if reason == "retention-excess" else marked_selection).add(out_tag)

    effective_retention = resolve_archive(plan.expanded.archive, retention_global)
    result = reconcile_import(
        plan.expanded,
        source,
        mirror,
        now,
        transform_versions=transform_versions,
        marked_selection=marked_selection,
        marked_retention=marked_retention,
        retention=effective_retention,
    )
    effective_mode = resolve_deletion_mode(
        plan.policy.spec.deletion_mode, plan.config.deletion_mode, deletion_mode
    )

    def emit_applied(op: Operation, variant: str) -> None:
        reporter.operation_applied(
            OperationEvent(
                policy=policy_name,
                dest_repo=plan.dest_repo,
                variant=variant,
                kind=op.kind,
                out_tag=op.out_tag,
                src_tag=op.src_tag,
                digest=op.digest,
                applied=op.applied,
                transform_steps=tuple(op.transform_steps) if op.transform_steps else None,
                out_digest=op.out_digest,
            )
        )

    def emit_failed(op: Operation, variant: str, error: ErrorInfo) -> None:
        reporter.operation_failed(
            OperationEvent(
                policy=policy_name,
                dest_repo=plan.dest_repo,
                variant=variant,
                kind=op.kind,
                out_tag=op.out_tag,
                src_tag=op.src_tag,
                digest=op.digest,
                applied=False,
            ),
            error,
        )

    def _attach_sbom(out_digest: str, out_tag: str, formats: list[str]) -> None:
        assert sbom_generator is not None  # callers guard: formats non-empty => generator wired
        placed = f"{plan.dest_repo}@{out_digest}"
        for d in sbom_generator.generate(
            placed,
            formats,
            tls_verify=plan.config.tls_verify,
            username=plan.config.username,
            password=(plan.config.password.get_secret_value() if plan.config.password else None),
            ca_cert=plan.config.ca_cert,
        ):
            registry.put_referrer(
                placed,
                d.media_type,  # artifactType == media type (discoverable)
                build_sbom_annotations(
                    prefix=label_prefix,
                    subject_digest=out_digest,
                    fmt=d.format,
                    tool="syft",
                    tool_version=d.tool_version,
                    timestamp=now,
                ),
                blob=d.content,
                media_type=d.media_type,
            )
            if attestor is not None:
                attestor.attest(
                    placed,
                    build_sbom_statement(
                        subject_name=f"{plan.dest_repo}:{out_tag}",
                        subject_digest=out_digest,
                        fmt=d.format,
                        content=d.content,
                    ),
                )

    def _attest(
        out_digest: str, *, variant: str, vplan: VariantPlan, out_tag: str, source_digest: str
    ) -> None:
        assert attestor is not None  # callers guard on attestor before calling
        attestor.attest(
            f"{plan.dest_repo}@{out_digest}",
            build_transform_statement(
                subject_name=f"{plan.dest_repo}:{out_tag}",
                subject_digest=out_digest,
                policy=plan.policy.metadata.name,
                import_name=plan.expanded.name,
                variant=variant,
                source=src_repo,
                source_digest=source_digest,
                builder_id=attest_builder_id,
                created=now.isoformat(),
                transform_version=transform_versions.get(vplan.name) or "",
                steps=[(s.name, s.params) for s in vplan.transform],
                transformed=bool(vplan.transform),
            ),
        )

    def _do_import(w: _ImportWork) -> Operation:
        steps = [s.name for s in w.vplan.transform] or None  # applied steps; None on a copy
        try:
            out_digest: str | None = None
            dest_ref = f"{plan.dest_repo}:{w.out_tag}"
            if not dry_run_tags:
                if w.vplan.transform:
                    _build_variant(
                        builder=builder,
                        source_ref=f"{src_repo}@{source[w.src_tag].digest}",
                        dest_ref=dest_ref,
                        resolved=plan.transforms[w.vplan.name],
                        platform=build_platform,
                        work_dir=work_dir,
                        provenance=attestor is not None,
                        tls_verify=plan.config.tls_verify,
                    )
                    # buildkit's output digest is not known until the tag is resolved, so
                    # the rebuild path stamps in place.
                    stamp_ref, publish_as = dest_ref, None
                else:
                    # Digest-pinned like the rebuild path above: copying by tag would let an
                    # upstream retag between plan and apply place bytes the stamp below does
                    # not describe. The destination ref keeps the tag, so the copy applies it.
                    registry.copy(f"{src_repo}@{source[w.src_tag].digest}", dest_ref)
                    # A plain copy transfers the manifest byte-for-byte, so the placed digest
                    # IS the source digest. Stamp that pinned ref and publish the result to
                    # the tag, rather than re-resolving a tag a concurrent writer could move
                    # between the copy and the stamp.
                    stamp_ref = f"{plan.dest_repo}@{source[w.src_tag].digest}"
                    publish_as = dest_ref
                out_digest = registry.annotate(
                    stamp_ref,
                    build_stamp_annotations(
                        prefix=label_prefix,
                        source_registry=registry_source.registry,
                        source_repository=registry_source.repository,
                        source_tag=w.src_tag,
                        source_digest=source[w.src_tag].digest,
                        source_revision=source[w.src_tag].revision,
                        created=now,
                        owners=plan.expanded.owners,
                        vendor=plan.expanded.vendor,
                        artifact_type=plan.policy.spec.artifact_type.value,
                        policy=plan.policy.metadata.name,
                        import_name=plan.expanded.name,
                        variant=w.variant,
                        transform_steps=steps,
                        transform_version_value=transform_versions.get(w.vplan.name),
                    ),
                    publish_as=publish_as,
                )
                # SBOM (both paths): scan the placed digest, attach one referrer per
                # configured format. Inside the try => a generation/attach failure fails
                # the op (no silently-uncovered image), like signing. Empty formats =>
                # skip (lib/test affordance; KNOCK_SBOM_FORMATS guarantees >=1 in prod).
                if sbom_formats and out_digest is not None:
                    _attach_sbom(out_digest, w.out_tag, sbom_formats)
                # Sign knock's predicate over the stamped output digest — rebuild AND copy
                # (the label is the product: every placed image is signed). Inside the try =>
                # a signing failure fails the operation rather than leaving a silent gap.
                if attestor is not None and out_digest is not None:
                    _attest(
                        out_digest,
                        variant=w.variant,
                        vplan=w.vplan,
                        out_tag=w.out_tag,
                        source_digest=source[w.src_tag].digest,
                    )
            op = Operation(
                kind=w.kind,
                out_tag=w.out_tag,
                src_tag=w.src_tag,
                digest=source[w.src_tag].digest,
                applied=not dry_run_tags,
                transform_steps=steps,
                out_digest=out_digest,
            )
            emit_applied(op, w.variant)
            return op
        except Exception as exc:
            info = ErrorInfo(
                type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc)
            )
            op = Operation(
                kind=w.kind,
                out_tag=w.out_tag,
                src_tag=w.src_tag,
                digest=source[w.src_tag].digest,
                applied=False,
                error=info,
                transform_steps=steps,
            )
            emit_failed(op, w.variant, info)
            return op

    def _do_sign(w: _SignWork) -> Operation:
        src_digest = source[w.src_tag].digest
        try:
            out_digest: str | None = None
            if not dry_run_tags:
                out_digest = mirror_digests[w.out_tag]
                # to_sign is empty unless an attestor is configured
                _attest(
                    out_digest,
                    variant=w.variant,
                    vplan=w.vplan,
                    out_tag=w.out_tag,
                    source_digest=mirror[w.out_tag].base_digest,
                )
            op = Operation(
                kind="attested",
                out_tag=w.out_tag,
                src_tag=w.src_tag,
                digest=src_digest,
                applied=not dry_run_tags,
                out_digest=out_digest,
            )
            emit_applied(op, w.variant)
            return op
        except Exception as exc:
            info = ErrorInfo(
                type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc)
            )
            op = Operation(
                kind="attested",
                out_tag=w.out_tag,
                src_tag=w.src_tag,
                digest=src_digest,
                applied=False,
                error=info,
            )
            emit_failed(op, w.variant, info)
            return op

    def _do_sbom(w: _SbomWork) -> Operation:
        src_digest = source[w.src_tag].digest
        try:
            out_digest: str | None = None
            if not dry_run_tags:
                out_digest = mirror_digests[w.out_tag]  # the live digest — no rebuild
                _attach_sbom(out_digest, w.out_tag, w.formats)
            op = Operation(
                kind="sbom",
                out_tag=w.out_tag,
                src_tag=w.src_tag,
                digest=src_digest,
                applied=not dry_run_tags,
                out_digest=out_digest,
            )
            emit_applied(op, w.variant)
            return op
        except Exception as exc:
            info = ErrorInfo(
                type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc)
            )
            op = Operation(
                kind="sbom",
                out_tag=w.out_tag,
                src_tag=w.src_tag,
                digest=src_digest,
                applied=False,
                error=info,
            )
            emit_failed(op, w.variant, info)
            return op

    def _do_alias(w: _AliasWork) -> Operation:
        try:
            if not dry_run_tags:
                registry.copy(f"{plan.dest_repo}:{w.target}", f"{plan.dest_repo}:{w.alias}")
            op = Operation(
                kind="aliased",
                out_tag=w.alias,
                src_tag=w.target,
                digest=None,
                applied=not dry_run_tags,
            )
            emit_applied(op, w.variant)
            return op
        except Exception as exc:
            info = ErrorInfo(
                type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc)
            )
            op = Operation(
                kind="aliased",
                out_tag=w.alias,
                src_tag=w.target,
                digest=None,
                applied=False,
                error=info,
            )
            emit_failed(op, w.variant, info)
            return op

    def _do_delete(w: _DeleteWork) -> Operation:
        try:
            if not dry_run_deletions:
                registry.delete_tag(f"{plan.dest_repo}:{w.out_tag}")
            op = Operation(
                kind="deleted",
                out_tag=w.out_tag,
                src_tag=None,
                digest=None,
                applied=not dry_run_deletions,
            )
            emit_applied(op, "")
            return op
        except Exception as exc:
            info = ErrorInfo(
                type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc)
            )
            op = Operation(
                kind="deleted",
                out_tag=w.out_tag,
                src_tag=None,
                digest=None,
                applied=False,
                error=info,
            )
            emit_failed(op, "", info)
            return op

    def _do_mark(w: _DeleteWork) -> Operation:
        try:
            if not dry_run_deletions:
                registry.put_referrer(
                    f"{plan.dest_repo}:{w.out_tag}",
                    PENDING_DELETION_ARTIFACT_TYPE,
                    build_pending_deletion_annotations(
                        prefix=label_prefix,
                        marked_at=now,
                        reason=w.reason,
                        policy=plan.policy.metadata.name,
                        import_name=plan.expanded.name,
                    ),
                )
            op = Operation(
                kind="marked",
                out_tag=w.out_tag,
                src_tag=None,
                digest=None,
                applied=not dry_run_deletions,
            )
            emit_applied(op, "")
            return op
        except Exception as exc:
            info = ErrorInfo(
                type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc)
            )
            op = Operation(
                kind="marked",
                out_tag=w.out_tag,
                src_tag=None,
                digest=None,
                applied=False,
                error=info,
            )
            emit_failed(op, "", info)
            return op

    # Stage 1: imports/updates, all variants flattened, input order preserved.
    import_items: list[_ImportWork] = []
    sign_items: list[_SignWork] = []
    sbom_items: list[_SbomWork] = []
    for vr, vplan in zip(result.variants, plan.expanded.variants, strict=True):
        out_to_src = {t + vplan.suffix: t for t in vplan.tags}
        for out_tag in [*vr.to_import, *vr.to_update]:
            kind: OperationKind = "imported" if out_tag in vr.to_import else "updated"
            import_items.append(
                _ImportWork(
                    variant=vr.variant,
                    vplan=vplan,
                    out_tag=out_tag,
                    src_tag=out_to_src[out_tag],
                    kind=kind,
                )
            )
        for out_tag in vr.to_sign:
            sign_items.append(
                _SignWork(
                    variant=vr.variant,
                    vplan=vplan,
                    out_tag=out_tag,
                    src_tag=out_to_src[out_tag],
                )
            )
        for out_tag in vr.to_sbom:
            sbom_items.append(
                _SbomWork(
                    variant=vr.variant,
                    vplan=vplan,
                    out_tag=out_tag,
                    src_tag=out_to_src[out_tag],
                    # to_sbom ⟺ missing_sbom (same probe loop) — index, don't .get(): a
                    # missing key is an invariant violation, not "re-attach every format".
                    formats=missing_sbom[out_tag],
                )
            )
    import_ops = _run_stage(import_items, _do_import, executor=executor)
    # Barrier. Backfill stage: sign skipped-but-unsigned mirror tags (already up-to-date).
    sign_ops = _run_stage(sign_items, _do_sign, executor=executor)
    # Barrier. Backfill stage: attach missing SBOM referrers on already-placed digests.
    sbom_ops = _run_stage(sbom_items, _do_sbom, executor=executor)

    # Barrier. Stage 2: aliases (depend on the imported targets).
    alias_items: list[_AliasWork] = []
    for vr in result.variants:
        for alias_name, target in vr.aliases.items():
            alias_items.append(_AliasWork(variant=vr.variant, alias=alias_name, target=target))
    alias_ops = _run_stage(alias_items, _do_alias, executor=executor)

    # Barrier. Stage 3: lifecycle (target-level) — selection (purge OR mark) + retention.
    if effective_mode == DeletionMode.mark:
        mark_items = [_DeleteWork(out_tag=t) for t in result.to_delete if t not in marked_referrers]
        selection_ops = _run_stage(mark_items, _do_mark, executor=executor)
    else:
        delete_items = [_DeleteWork(out_tag=t) for t in result.to_delete]
        selection_ops = _run_stage(delete_items, _do_delete, executor=executor)
    # Retention ALWAYS marks (never hard-deletes), regardless of deletion_mode; skip
    # already-marked (idempotent). The usage-gated reaper (`knock purge`) owns removal.
    retention_items = [
        _DeleteWork(out_tag=t, reason="retention-excess")
        for t in result.to_mark_retention
        if t not in marked_retention
    ]
    retention_ops = _run_stage(retention_items, _do_mark, executor=executor)
    lifecycle_ops = selection_ops + retention_ops

    # Auto-unmark (mode-independent): tags that re-entered the desired set lose any
    # stale knock pending-deletion mark — runs in purge mode too. Quiet cleanup (no
    # event); best-effort so a transient failure doesn't fail the target (retried next run).
    if not dry_run_deletions:
        for out_tag, want_retention in (
            *((t, False) for t in result.to_unmark),
            *((t, True) for t in result.to_unmark_retention),
        ):
            for ref in marked_referrers.get(out_tag, []):
                reason = parse_pending_mark(label_prefix, out_tag, ref.annotations).reason
                if (reason == "retention-excess") != want_retention:
                    continue  # clear only the referrer for the axis being unmarked
                try:
                    registry.delete_referrer(f"{plan.dest_repo}@{ref.digest}")
                except Exception:  # noqa: S110 — best-effort cleanup, retried next run
                    pass

    # Reassemble per-variant reports, preserving input order.
    imports_by_variant: dict[str, list[Operation]] = defaultdict(list)
    for it, op in zip(import_items, import_ops, strict=True):
        imports_by_variant[it.variant].append(op)
    aliases_by_variant: dict[str, list[Operation]] = defaultdict(list)
    for ait, op in zip(alias_items, alias_ops, strict=True):
        aliases_by_variant[ait.variant].append(op)
    attested_by_variant: dict[str, list[Operation]] = defaultdict(list)
    for sit, op in zip(sign_items, sign_ops, strict=True):
        attested_by_variant[sit.variant].append(op)
    sbom_by_variant: dict[str, list[Operation]] = defaultdict(list)
    for sbit, op in zip(sbom_items, sbom_ops, strict=True):
        sbom_by_variant[sbit.variant].append(op)

    variant_reports: list[VariantReport] = []
    for vr, vplan in zip(result.variants, plan.expanded.variants, strict=True):
        changed = set(vr.to_import) | set(vr.to_update) | set(vr.to_sign) | set(vr.to_sbom)
        ops: list[Operation] = list(imports_by_variant[vr.variant])
        ops.extend(attested_by_variant[vr.variant])
        ops.extend(sbom_by_variant[vr.variant])
        for tag in vplan.tags:
            out_tag = tag + vplan.suffix
            if out_tag not in changed:
                sop = Operation(
                    kind="skipped",
                    out_tag=out_tag,
                    src_tag=tag,
                    digest=source[tag].digest,
                    applied=False,
                )
                ops.append(sop)
                emit_applied(sop, vr.variant)
        ops.extend(aliases_by_variant[vr.variant])
        variant_reports.append(
            VariantReport(
                name=vr.variant,
                suffix=vplan.suffix,
                status=node_status(ops),
                totals=counts_of(ops),
                operations=ops,
            )
        )

    target_ops_all = [op for v in variant_reports for op in v.operations] + lifecycle_ops
    target_totals = merge_counts([v.totals for v in variant_reports] + [counts_of(lifecycle_ops)])
    return TargetReport(
        dest_repo=plan.dest_repo,
        status=node_status(target_ops_all),
        variants=variant_reports,
        operations=lifecycle_ops,
        totals=target_totals,
    )


@dataclass
class RegistryPlanner:
    """The registry-sourced planner: plan every policy it owns, then apply.

    Satisfies `PolicyPlanner` structurally. The two method bodies are the plan and
    apply loops `reconcile_policies` used to run inline, lifted verbatim; it now
    dispatches to them here.

    The split of `reconcile_policies`' parameters follows the rule `PolicyPlanner`
    states: what the DRIVER owns or shares across planners stays a method parameter
    (`policies`, `reporter`, `executor`), and what only this planner needs is a
    constructor field. So `reporter` and `executor` are parameters of `apply`, and
    `max_concurrency` / `shard_index` / `shard_count` are not here at all — the
    driver resolves them before dispatching.
    """

    registry: RegistryPort
    builder: ImageBuilderPort
    roster: dict[str, RegistryConfig]
    ca_certs: dict[str, CACertSource]
    package_mirrors: dict[str, PackageMirror]
    build_platform: str
    now: datetime
    label_prefix: str
    dry_run_tags: bool
    dry_run_deletions: bool
    deletion_mode: DeletionMode = DeletionMode.purge
    work_dir: Path | None = None
    attestor: AttestorPort | None = None
    attest_builder_id: str = ""
    sbom_generator: SbomGeneratorPort | None = None
    sbom_formats: list[str] = field(default_factory=list)
    retention_global: Archive | None = None
    # A field rather than a local because `plan` and `apply` must share ONE session
    # set: `plan` logs into the source registries, `apply` into the destinations, and
    # a second set would re-login hosts already configured. Public so a driver can
    # pass the SAME set to every planner — the git planner also takes `registry` +
    # `roster`, and would otherwise re-login the very hosts this one just did.
    logged_in: set[str] = field(default_factory=set)
    # None until `plan` runs — see the guard in `apply`. `init=False` keeps it out of
    # the constructor, `repr=False` out of every log line and exception repr (it holds
    # each MirrorPolicy and RegistryConfig in the batch).
    _plans: list[tuple[MirrorPolicy, list[_Plan]]] | None = field(
        default=None, init=False, repr=False
    )

    def handles(self, policy: MirrorPolicy) -> bool:
        return isinstance(policy.spec.source, RegistrySource)

    def plan(self, policies: list[MirrorPolicy]) -> list[AliasTarget]:
        alias_entries: list[AliasTarget] = []
        plans: list[tuple[MirrorPolicy, list[_Plan]]] = []
        for policy in policies:
            # Configure the source registry's TLS/auth (from the roster) before listing its tags —
            # a plain-HTTP or custom-CA source registry otherwise fails the plan-phase `tag ls`.
            # Sources not in the roster (public upstreams like docker.io) keep ambient HTTPS config.
            src_repo = _source_repo(policy)
            src_match = match_registry_by_host(src_repo, self.roster)
            if src_match is not None:
                ensure_registry_session(self.registry, src_match[1], self.logged_in)
            # Drop the referrers-tag-schema fallbacks the same way (see the destination walk):
            # a `sha256-<digest>` tag is a referrer manifest, never an image to mirror.
            src_tags = [
                t for t in self.registry.list_tags(src_repo) if not is_referrers_fallback_tag(t)
            ]
            policy_plans: list[_Plan] = []
            for resolved in resolve_imports(policy.spec):
                expanded = expand_import(resolved, src_tags)
                for v in expanded.variants:
                    validate_transform_steps(v.transform)
                transforms = {
                    v.name: _resolve_transform(v.transform, self.ca_certs, self.package_mirrors)
                    for v in expanded.variants
                    if v.transform
                }
                for dest in resolved.destinations or []:
                    _name, cfg = resolve_registry(dest.registry, self.roster)
                    dest_repo = f"{cfg.host}/{dest.project}/{dest.repository}"
                    policy_plans.append(
                        _Plan(
                            policy=policy,
                            expanded=expanded,
                            dest_repo=dest_repo,
                            config=cfg,
                            transforms=transforms,
                        )
                    )
                    for variant in expanded.variants:
                        for alias_name, target in variant.aliases.items():
                            alias_entries.append(
                                AliasTarget(
                                    dest_repo=dest_repo,
                                    alias=alias_name + variant.suffix,
                                    target=target + variant.suffix,
                                )
                            )
            plans.append((policy, policy_plans))
        # Assign, never append: a second `plan` call replaces the batch rather than
        # silently doubling the work a later `apply` would do.
        self._plans = plans
        return alias_entries

    def apply(
        self, *, reporter: Reporter, executor: ThreadPoolExecutor | None
    ) -> list[PolicyReport]:
        if self._plans is None:
            # A driver that applies a batch it never planned would report a clean
            # empty run — silently placing nothing. Loud instead.
            raise InternalError("RegistryPlanner.apply called before plan")
        policy_reports: list[PolicyReport] = []
        for policy, policy_plans in self._plans:
            source_ref = _source_repo(policy)
            reporter.policy_started(policy.metadata.name, source_ref)
            try:
                targets: list[TargetReport] = []
                for plan in policy_plans:
                    cfg = plan.config
                    ensure_registry_session(self.registry, cfg, self.logged_in)
                    targets.append(
                        _apply_plan(
                            plan,
                            registry=self.registry,
                            builder=self.builder,
                            label_prefix=self.label_prefix,
                            build_platform=self.build_platform,
                            work_dir=self.work_dir,
                            now=self.now,
                            dry_run_tags=self.dry_run_tags,
                            dry_run_deletions=self.dry_run_deletions,
                            deletion_mode=self.deletion_mode,
                            reporter=reporter,
                            policy_name=policy.metadata.name,
                            executor=executor,
                            attestor=self.attestor,
                            attest_builder_id=self.attest_builder_id,
                            sbom_generator=self.sbom_generator,
                            sbom_formats=self.sbom_formats,
                            retention_global=self.retention_global,
                        )
                    )
                all_ops = [op for t in targets for v in t.variants for op in v.operations] + [
                    op for t in targets for op in t.operations
                ]
                totals = merge_counts([t.totals for t in targets])
                reporter.policy_completed(policy.metadata.name, totals)
                policy_reports.append(
                    PolicyReport(
                        name=policy.metadata.name,
                        source=source_ref,
                        status=node_status(all_ops),
                        error=None,
                        totals=totals,
                        targets=targets,
                    )
                )
            except Exception as exc:
                info = ErrorInfo(
                    type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc)
                )
                reporter.policy_failed(policy.metadata.name, info)
                policy_reports.append(
                    PolicyReport(
                        name=policy.metadata.name,
                        source=source_ref,
                        status="failed",
                        error=info,
                        totals=Counts(),
                        targets=[],
                    )
                )

        return policy_reports
