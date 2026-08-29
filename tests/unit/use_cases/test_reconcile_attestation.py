from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from knock.adapters.local_archiver import LocalArchiver
from knock.config import CACertSource, PackageMirror, RegistryConfig
from knock.domain.attestation import COSIGN_ATTESTATION_ARTIFACT_TYPE, PREDICATE_TYPE
from knock.domain.mirror_policy import MirrorPolicy, parse_mirror_policy
from knock.ports.registry import ImageInfo, Referrer
from knock.use_cases.reconcile import reconcile_policies
from knock.use_cases.report import RunReport
from tests.fakes.attestor import FakeAttestor
from tests.fakes.image_builder import FakeImageBuilder
from tests.fakes.registry import FakeRegistryPort
from tests.fakes.reporter import FakeReporter
from tests.fakes.source import FakeSourcePort

NOW = datetime(2026, 6, 11, tzinfo=UTC)


def _hardened_policy() -> MirrorPolicy:
    return parse_mirror_policy(Path("docs/examples/hardened/redis.yml").read_text())


def _copy_policy() -> MirrorPolicy:
    return parse_mirror_policy(
        """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: busybox-copy }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/busybox }
  imports:
    - name: stable
      tags: { includeRegex: "^1\\\\.36\\\\.0$" }
      destinations: [{ project: demo, repository: busybox }]
"""
    )


def _run(policy: MirrorPolicy, registry: FakeRegistryPort, **over: object) -> RunReport:
    kwargs: dict[str, object] = dict(
        registry=registry,
        builder=FakeImageBuilder(),
        source=FakeSourcePort(),
        archiver=LocalArchiver(),
        roster={"local": RegistryConfig(host="reg.local")},
        ca_certs={"corp": CACertSource(pem="PEMDATA")},
        package_mirrors={"corp": PackageMirror(apt="https://mirror.corp")},
        build_platform="linux/amd64",
        now=NOW,
        label_prefix="io.knock",
        dry_run_tags=False,
        dry_run_deletions=False,
        reporter=FakeReporter(),
        attest_builder_id="https://knock.example/builders/main",
    )
    kwargs.update(over)
    return reconcile_policies([policy], **kwargs)  # type: ignore[arg-type]


def _hardened_registry() -> FakeRegistryPort:
    src = "docker.io/library/redis"
    return FakeRegistryPort(
        tags={src: ["7.2.5"], "reg.local/hardened/redis": []},
        infos={f"{src}:7.2.5": ImageInfo(digest="sha256:src", created=NOW, annotations={})},
    )


def test_rebuild_attests_with_transform_predicate() -> None:
    registry = _hardened_registry()
    builder = FakeImageBuilder()
    attestor = FakeAttestor()
    report = _run(_hardened_policy(), registry, builder=builder, attestor=attestor)

    assert report.totals.imported == 1
    assert builder.requests[0].provenance is True
    assert len(attestor.attested) == 1
    subject_ref, statement = attestor.attested[0]
    out_digest = registry.annotate(
        "reg.local/hardened/redis:7.2.5", {}
    )  # deterministic fake digest
    assert subject_ref == f"reg.local/hardened/redis@{out_digest}"
    assert statement["predicateType"] == PREDICATE_TYPE
    pred = statement["predicate"]
    assert pred["policy"] == "redis-hardened"
    assert pred["import"] == "v7"
    assert pred["source"] == "docker.io/library/redis"
    assert pred["source_digest"] == "sha256:src"
    assert pred["builder_id"] == "https://knock.example/builders/main"
    assert [s["name"] for s in pred["steps"]] == ["injectCA", "rewritePackageSources"]
    assert pred["transformed"] is True


def test_copy_path_signs_with_transformed_false() -> None:
    # A copy variant (no transform) importing a NEW tag must produce ONE attest call
    # whose predicate carries transformed=False and empty steps.
    src = "docker.io/library/busybox"
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], "reg.local/demo/busybox": []},
        infos={f"{src}:1.36.0": ImageInfo(digest="sha256:s", created=NOW, annotations={})},
    )
    builder = FakeImageBuilder()
    attestor = FakeAttestor()
    _run(_copy_policy(), registry, builder=builder, attestor=attestor)
    assert builder.requests == []  # copy path does not rebuild
    assert len(attestor.attested) == 1
    _subject, statement = attestor.attested[-1]
    assert statement["predicate"]["transformed"] is False
    assert statement["predicate"]["steps"] == []


def test_skipped_unattested_tag_is_backfilled_once() -> None:
    # Tag already mirrored (source unchanged), NO existing attestation referrer seeded.
    # => exactly one attest call against the mirror's CURRENT digest, op kind "attested".
    src = "docker.io/library/busybox"
    dest_repo = "reg.local/demo/busybox"
    mirror_digest = "sha256:mirrordigest"
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], dest_repo: ["1.36.0"]},
        infos={
            f"{src}:1.36.0": ImageInfo(digest="sha256:s", created=NOW, annotations={}),
            f"{dest_repo}:1.36.0": ImageInfo(
                digest=mirror_digest,
                created=NOW,
                annotations={"org.opencontainers.image.base.digest": "sha256:s"},
            ),
        },
    )
    attestor = FakeAttestor()
    report = _run(_copy_policy(), registry, attestor=attestor)
    assert len(attestor.attested) == 1
    subject, _ = attestor.attested[0]
    assert subject == f"{dest_repo}@{mirror_digest}"
    ops = [op for v in report.policies[0].targets[0].variants for op in v.operations]
    assert any(op.kind == "attested" and op.error is None for op in ops)


def test_skipped_attested_tag_is_not_resigned() -> None:
    # Same as above but a cosign attestation referrer is already on the mirror tag =>
    # the tag classifies as attested and is NOT re-signed.
    src = "docker.io/library/busybox"
    dest_repo = "reg.local/demo/busybox"
    mirror_digest = "sha256:mirrordigest"
    referrers = {
        f"{dest_repo}:1.36.0": [
            Referrer(
                digest="sha256:bundle",
                artifact_type=COSIGN_ATTESTATION_ARTIFACT_TYPE,
                annotations={},
                subject_tag="1.36.0",
            )
        ]
    }
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], dest_repo: ["1.36.0"]},
        infos={
            f"{src}:1.36.0": ImageInfo(digest="sha256:s", created=NOW, annotations={}),
            f"{dest_repo}:1.36.0": ImageInfo(
                digest=mirror_digest,
                created=NOW,
                annotations={"org.opencontainers.image.base.digest": "sha256:s"},
            ),
        },
        referrers=referrers,
    )
    attestor = FakeAttestor()
    _run(_copy_policy(), registry, attestor=attestor)
    assert attestor.attested == []


def test_no_attestor_means_no_provenance() -> None:
    registry = _hardened_registry()
    builder = FakeImageBuilder()
    _run(_hardened_policy(), registry, builder=builder, attestor=None)
    assert builder.requests[0].provenance is False


def test_dry_run_does_not_attest() -> None:
    registry = _hardened_registry()
    attestor = FakeAttestor()
    _run(_hardened_policy(), registry, attestor=attestor, dry_run_tags=True, dry_run_deletions=True)
    assert attestor.attested == []


def test_attestation_failure_fails_the_operation() -> None:
    registry = _hardened_registry()
    report = _run(_hardened_policy(), registry, attestor=FakeAttestor(fail=True))
    assert report.status == "failed"
    op = report.policies[0].targets[0].variants[0].operations[0]
    assert op.error is not None
    assert op.error.type == "CosignError"
    assert op.error.exit_code == 2


def test_backfill_records_mirror_base_digest_not_current_source() -> None:
    # The mirror tag exists (base_digest = "sha256:old"), the source has since moved
    # to "sha256:new", but the push was RECENT (within the 7-day grace window) so the
    # tag is classified for signing (to_sign), NOT for update.
    # The backfill predicate must record source_digest == "sha256:old" (what the mirror
    # was actually derived from), NOT "sha256:new" (the current source).
    from datetime import timedelta

    from knock.domain.reconcile import DEFAULT_GRACE

    src = "docker.io/library/busybox"
    dest_repo = "reg.local/demo/busybox"
    mirror_digest = "sha256:mirrordigest"
    # Source moved very recently (well within the grace window) — classifies as "sign".
    recent_push = NOW - DEFAULT_GRACE + timedelta(hours=1)
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], dest_repo: ["1.36.0"]},
        infos={
            # Current source has a NEW digest
            f"{src}:1.36.0": ImageInfo(digest="sha256:new", created=recent_push, annotations={}),
            # Mirror was derived from the OLD digest
            f"{dest_repo}:1.36.0": ImageInfo(
                digest=mirror_digest,
                created=NOW,
                annotations={"org.opencontainers.image.base.digest": "sha256:old"},
            ),
        },
    )
    attestor = FakeAttestor()
    _run(_copy_policy(), registry, attestor=attestor)
    assert len(attestor.attested) == 1
    _subject, statement = attestor.attested[0]
    # Must record the OLD digest (what the mirror was derived from), not "sha256:new"
    assert statement["predicate"]["source_digest"] == "sha256:old", (
        f"expected sha256:old but got {statement['predicate']['source_digest']!r}"
    )


def test_backfill_attestation_failure_is_visible() -> None:
    # Tag already mirrored (source unchanged), NO existing attestation referrer seeded,
    # but the attestor is configured to fail => the backfill attempt must surface a
    # FAILED "attested" operation (no silent gap).
    src = "docker.io/library/busybox"
    dest_repo = "reg.local/demo/busybox"
    mirror_digest = "sha256:mirrordigest"
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], dest_repo: ["1.36.0"]},
        infos={
            f"{src}:1.36.0": ImageInfo(digest="sha256:s", created=NOW, annotations={}),
            f"{dest_repo}:1.36.0": ImageInfo(
                digest=mirror_digest,
                created=NOW,
                annotations={"org.opencontainers.image.base.digest": "sha256:s"},
            ),
        },
    )
    report = _run(_copy_policy(), registry, attestor=FakeAttestor(fail=True))
    ops = [op for v in report.policies[0].targets[0].variants for op in v.operations]
    attested_ops = [op for op in ops if op.kind == "attested"]
    assert len(attested_ops) == 1
    assert attested_ops[0].error is not None
