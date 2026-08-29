"""Reconcile use case: orchestrate selection → expand → reconcile → apply against
real registries. Tags are mirrored by copy, or rebuilt through a hardening
transform, then stamped. Returns a structured RunReport and emits in-flight
events through the Reporter port. Depends only on ports.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from knock.config import (
    CACertSource,
    PackageMirror,
    RegistryConfig,
    match_registry_by_host,
    resolve_registry,
)
from knock.domain.collision import (
    AliasTarget,
    detect_alias_collisions,
    detect_dest_repo_collisions,
)
from knock.domain.deletion_mode import DeletionMode
from knock.domain.expand import expand_import
from knock.domain.mirror_policy import Archive, GitSource, MirrorPolicy, RegistrySource
from knock.domain.policy_merge import resolve_imports
from knock.domain.scan.refs import is_referrers_fallback_tag
from knock.domain.sharding import owns
from knock.domain.transforms.render import validate_transform_steps
from knock.errors import UnsupportedSourceError, exit_code_for
from knock.ports.attestor import AttestorPort
from knock.ports.image_builder import ImageBuilderPort
from knock.ports.registry import RegistryPort
from knock.ports.reporter import Counts, ErrorInfo, Reporter
from knock.ports.sbom import SbomGeneratorPort
from knock.use_cases.reconcile_registry import (
    Plan,
    apply_plan,
    resolve_transform,
    source_repo,
)
from knock.use_cases.registry_session import ensure_registry_session
from knock.use_cases.report import (
    PolicyReport,
    RunMode,
    RunReport,
    RunStatus,
    TargetReport,
    merge_counts,
    node_status,
)


def _skipped_source_report(policy: MirrorPolicy, reporter: Reporter) -> PolicyReport:
    """Report and record a policy whose source this use case does not reconcile yet.

    Both source kinds are legitimate `MirrorPolicy` shapes (see mirror_policy.py); this
    use case just doesn't know how to mirror a git source. Never raises: the whole
    point is that one such policy must not abort the run for every other policy in the
    worklist, and must not vanish from the report silently either — an operator needs
    to see why it did nothing.
    """
    source = policy.spec.source
    assert isinstance(source, GitSource)  # the only non-RegistrySource member of Source
    exc = UnsupportedSourceError(
        f"policy '{policy.metadata.name}' is git-sourced; not handled by reconcile"
    )
    info = ErrorInfo(type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc))
    reporter.policy_started(policy.metadata.name, source.url)
    reporter.policy_failed(policy.metadata.name, info)
    return PolicyReport(
        name=policy.metadata.name,
        source=source.url,
        status="failed",
        error=info,
        totals=Counts(),
        targets=[],
    )


def _resolved_dest_repos(policy: MirrorPolicy, roster: dict[str, RegistryConfig]) -> list[str]:
    """Every destination repo a policy writes to, resolved against the roster.
    Pure: uses resolve_imports + resolve_registry only (no expand, no registry calls)."""
    repos: list[str] = []
    for resolved in resolve_imports(policy.spec):
        for dest in resolved.destinations or []:
            _name, cfg = resolve_registry(dest.registry, roster)
            repos.append(f"{cfg.host}/{dest.project}/{dest.repository}")
    return repos


def reconcile_policies(
    policies: list[MirrorPolicy],
    *,
    registry: RegistryPort,
    builder: ImageBuilderPort,
    roster: dict[str, RegistryConfig],
    ca_certs: dict[str, CACertSource],
    package_mirrors: dict[str, PackageMirror],
    build_platform: str,
    now: datetime,
    label_prefix: str,
    dry_run_tags: bool,
    dry_run_deletions: bool,
    reporter: Reporter,
    deletion_mode: DeletionMode = DeletionMode.purge,
    work_dir: Path | None = None,
    max_concurrency: int = 1,
    shard_index: int = 0,
    shard_count: int = 1,
    attestor: AttestorPort | None = None,
    attest_builder_id: str = "",
    sbom_generator: SbomGeneratorPort | None = None,
    sbom_formats: list[str] | None = None,
    retention_global: Archive | None = None,
) -> RunReport:
    mode: RunMode = "dry-run" if (dry_run_tags or dry_run_deletions) else "apply"
    sbom_formats = sbom_formats or []

    # --- Ownership invariant over ALL policies (pure, no I/O), then shard filter. ---
    # Every pod sees the full policy set (git-synced) and enforces one-owner-per-repo
    # identically; it then applies only the policies it owns. shard_count == 1 ⇒ all.
    owners = [
        (repo, policy.metadata.name)
        for policy in policies
        for repo in _resolved_dest_repos(policy, roster)
    ]
    detect_dest_repo_collisions(owners)
    policies = [
        p
        for p in policies
        if owns(p.metadata.name, shard_index=shard_index, shard_count=shard_count)
    ]

    # Registry-sourced only past this point: this use case doesn't yet know how to
    # mirror a git source (skill is git-only; generic may be either — see
    # mirror_policy.py's asymmetric source/artifactType rule). Split those out BEFORE
    # the plan phase touches `source_repo`, so one git-sourced policy in the worklist
    # can't abort reconciliation for every other policy — it is reported as skipped
    # instead (see `_skipped_source_report`), and the rest proceed normally.
    unsupported_policies = [p for p in policies if not isinstance(p.spec.source, RegistrySource)]
    policies = [p for p in policies if isinstance(p.spec.source, RegistrySource)]

    # --- Plan phase (fail-fast): expand, resolve destinations + transforms, collision-check.
    # Transform resolution (unknown cert/mirror names, unreadable cert files) surfaces all
    # config errors here, before ANY mutation. ---
    plans_by_policy: list[tuple[MirrorPolicy, list[Plan]]] = []
    alias_entries: list[AliasTarget] = []
    logged_in: set[str] = set()
    for policy in policies:
        # Configure the source registry's TLS/auth (from the roster) before listing its tags —
        # a plain-HTTP or custom-CA source registry otherwise fails the plan-phase `tag ls`.
        # Sources not in the roster (public upstreams like docker.io) keep ambient HTTPS config.
        src_repo = source_repo(policy)
        src_match = match_registry_by_host(src_repo, roster)
        if src_match is not None:
            ensure_registry_session(registry, src_match[1], logged_in)
        # Drop the referrers-tag-schema fallbacks the same way (see the destination walk):
        # a `sha256-<digest>` tag is a referrer manifest, never an image to mirror.
        src_tags = [t for t in registry.list_tags(src_repo) if not is_referrers_fallback_tag(t)]
        policy_plans: list[Plan] = []
        for resolved in resolve_imports(policy.spec):
            expanded = expand_import(resolved, src_tags)
            for v in expanded.variants:
                validate_transform_steps(v.transform)
            transforms = {
                v.name: resolve_transform(v.transform, ca_certs, package_mirrors)
                for v in expanded.variants
                if v.transform
            }
            for dest in resolved.destinations or []:
                _name, cfg = resolve_registry(dest.registry, roster)
                dest_repo = f"{cfg.host}/{dest.project}/{dest.repository}"
                policy_plans.append(
                    Plan(
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
        plans_by_policy.append((policy, policy_plans))
    detect_alias_collisions(alias_entries)  # fail fast before ANY mutation

    # --- Apply phase (isolated per policy). ---
    reporter.run_started(len(plans_by_policy) + len(unsupported_policies), mode=mode)
    policy_reports: list[PolicyReport] = [
        _skipped_source_report(p, reporter) for p in unsupported_policies
    ]
    with ExitStack() as stack:
        executor: ThreadPoolExecutor | None = (
            stack.enter_context(ThreadPoolExecutor(max_workers=max_concurrency))
            if max_concurrency > 1
            else None
        )
        for policy, policy_plans in plans_by_policy:
            source_ref = source_repo(policy)
            reporter.policy_started(policy.metadata.name, source_ref)
            try:
                targets: list[TargetReport] = []
                for plan in policy_plans:
                    cfg = plan.config
                    ensure_registry_session(registry, cfg, logged_in)
                    targets.append(
                        apply_plan(
                            plan,
                            registry=registry,
                            builder=builder,
                            label_prefix=label_prefix,
                            build_platform=build_platform,
                            work_dir=work_dir,
                            now=now,
                            dry_run_tags=dry_run_tags,
                            dry_run_deletions=dry_run_deletions,
                            deletion_mode=deletion_mode,
                            reporter=reporter,
                            policy_name=policy.metadata.name,
                            executor=executor,
                            attestor=attestor,
                            attest_builder_id=attest_builder_id,
                            sbom_generator=sbom_generator,
                            sbom_formats=sbom_formats,
                            retention_global=retention_global,
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

    statuses = [p.status for p in policy_reports]
    if all(s == "ok" for s in statuses):
        status: RunStatus = "ok"
    elif all(s == "failed" for s in statuses):
        status = "failed"
    else:
        status = "partial"
    report = RunReport(
        mode=mode,
        status=status,
        totals=merge_counts([p.totals for p in policy_reports]),
        policies=policy_reports,
    )
    reporter.run_completed(report)
    return report
