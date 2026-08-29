from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from knock.adapters.local_archiver import LocalArchiver
from knock.config import CACertSource, PackageMirror, RegistryConfig
from knock.domain.mirror_policy import MirrorPolicy, parse_mirror_policy
from knock.ports.registry import ImageInfo
from knock.use_cases.reconcile import reconcile_policies
from knock.use_cases.report import RunReport
from tests.fakes.attestor import FakeAttestor
from tests.fakes.image_builder import FakeImageBuilder
from tests.fakes.registry import FakeRegistryPort
from tests.fakes.reporter import FakeReporter
from tests.fakes.sbom_generator import FAKE_SYFT_VERSION, FakeSbomGenerator
from tests.fakes.source import FakeSourcePort

NOW = datetime(2026, 6, 17, tzinfo=UTC)

SPDX = "application/spdx+json"
CYCLONEDX = "application/vnd.cyclonedx+json"

SPDX_PREDICATE = "https://spdx.dev/Document"
CYCLONEDX_PREDICATE = "https://cyclonedx.org/bom"
SBOM_PREDICATES = {SPDX_PREDICATE, CYCLONEDX_PREDICATE}


def _sbom_attestations(attestor: FakeAttestor) -> list[tuple[str, dict]]:
    # attestor.attested journals (subject_ref, statement); keep only SBOM-typed ones
    # (the transform attestation, signed on the same digest, is filtered out).
    return [
        (ref, st) for ref, st in attestor.attested if st.get("predicateType") in SBOM_PREDICATES
    ]


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


def _hardened_policy() -> MirrorPolicy:
    return parse_mirror_policy(Path("docs/examples/hardened/redis.yml").read_text())


def _copy_registry() -> FakeRegistryPort:
    src = "docker.io/library/busybox"
    return FakeRegistryPort(
        tags={src: ["1.36.0"], "reg.local/demo/busybox": []},
        infos={f"{src}:1.36.0": ImageInfo(digest="sha256:bbsrc", created=NOW, annotations={})},
    )


def _hardened_registry() -> FakeRegistryPort:
    src = "docker.io/library/redis"
    return FakeRegistryPort(
        tags={src: ["7.2.5"], "reg.local/hardened/redis": []},
        infos={f"{src}:7.2.5": ImageInfo(digest="sha256:src", created=NOW, annotations={})},
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
    )
    kwargs.update(over)
    return reconcile_policies([policy], **kwargs)  # type: ignore[arg-type]


def _sbom_referrers(registry: FakeRegistryPort, subject: str) -> list[tuple[str, str]]:
    # artifact_referrers entries: (image_ref, artifact_type, media_type, blob, annotations)
    return [
        (r[1], r[2])
        for r in registry.artifact_referrers
        if r[0] == subject and r[1] in (SPDX, CYCLONEDX)
    ]


def test_copy_path_attaches_one_sbom_referrer_per_format() -> None:
    registry = _copy_registry()
    gen = FakeSbomGenerator()
    report = _run(
        _copy_policy(),
        registry,
        sbom_generator=gen,
        sbom_formats=["spdx-json", "cyclonedx-json"],
    )

    assert report.totals.imported == 1
    out_digest = registry.annotate("reg.local/demo/busybox:1.36.0", {})  # deterministic fake digest
    subject = f"reg.local/demo/busybox@{out_digest}"
    refs = _sbom_referrers(registry, subject)
    assert {media for _, media in refs} == {SPDX, CYCLONEDX}
    assert {art for art, _ in refs} == {SPDX, CYCLONEDX}  # artifactType == media type
    assert gen.calls == [(subject, ("spdx-json", "cyclonedx-json"), True)]


def test_rebuild_path_also_attaches_sbom_referrer() -> None:
    registry = _hardened_registry()
    report = _run(
        _hardened_policy(),
        registry,
        sbom_generator=FakeSbomGenerator(),
        sbom_formats=["spdx-json"],
    )

    assert report.totals.imported == 1
    out_digest = registry.annotate("reg.local/hardened/redis:7.2.5", {})
    subject = f"reg.local/hardened/redis@{out_digest}"
    assert _sbom_referrers(registry, subject) == [(SPDX, SPDX)]


def test_sbom_generation_failure_fails_the_op() -> None:
    registry = _copy_registry()
    report = _run(
        _copy_policy(),
        registry,
        sbom_generator=FakeSbomGenerator(fail=True),
        sbom_formats=["spdx-json"],
    )

    assert report.totals.imported == 0
    assert report.totals.failed == 1
    assert report.status != "ok"


def test_sbom_referrer_records_tool_version() -> None:
    registry = _copy_registry()
    _run(_copy_policy(), registry, sbom_generator=FakeSbomGenerator(), sbom_formats=["spdx-json"])

    out_digest = registry.annotate("reg.local/demo/busybox:1.36.0", {})
    subject = f"reg.local/demo/busybox@{out_digest}"
    # artifact_referrers entries: (image_ref, artifact_type, media_type, blob, annotations)
    ann = next(r[4] for r in registry.artifact_referrers if r[0] == subject)
    assert ann.get("io.knock.sbom.tool.version") == FAKE_SYFT_VERSION


def test_no_formats_means_no_sbom_calls() -> None:
    registry = _copy_registry()
    gen = FakeSbomGenerator()
    report = _run(_copy_policy(), registry, sbom_generator=gen)  # sbom_formats omitted

    assert report.totals.imported == 1
    assert gen.calls == []
    assert registry.artifact_referrers == []


def test_signs_one_sbom_attestation_per_format() -> None:
    registry = _copy_registry()
    attestor = FakeAttestor()
    report = _run(
        _copy_policy(),
        registry,
        sbom_generator=FakeSbomGenerator(),
        sbom_formats=["spdx-json", "cyclonedx-json"],
        attestor=attestor,
    )

    assert report.totals.imported == 1
    out_digest = registry.annotate("reg.local/demo/busybox:1.36.0", {})
    subject = f"reg.local/demo/busybox@{out_digest}"
    sboms = _sbom_attestations(attestor)
    assert {st["predicateType"] for _, st in sboms} == SBOM_PREDICATES
    assert all(ref == subject for ref, _ in sboms)
    assert all(st["subject"][0]["name"] == "reg.local/demo/busybox:1.36.0" for _, st in sboms)


def test_sbom_signing_is_gated_on_sbom_generation() -> None:
    # Attestor configured but no SBOM formats => transform is signed, SBOM is not.
    registry = _copy_registry()
    attestor = FakeAttestor()
    _run(_copy_policy(), registry, sbom_generator=FakeSbomGenerator(), attestor=attestor)

    assert _sbom_attestations(attestor) == []
    assert len(attestor.attested) == 1  # the transform attestation only


def test_skipped_uncovered_tag_is_sbom_backfilled_once() -> None:
    # Tag already mirrored (source unchanged), NO SBOM referrer seeded => exactly one
    # syft generate call against the mirror's CURRENT digest, referrer attached there,
    # op kind "sbom". No rebuild.
    src = "docker.io/library/busybox"
    dest_repo = "reg.local/demo/busybox"
    mirror_digest = "sha256:mirrordigest"
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], dest_repo: ["1.36.0"]},
        infos={
            f"{src}:1.36.0": ImageInfo(digest="sha256:bbsrc", created=NOW, annotations={}),
            f"{dest_repo}:1.36.0": ImageInfo(
                digest=mirror_digest,
                created=NOW,
                annotations={"org.opencontainers.image.base.digest": "sha256:bbsrc"},
            ),
        },
    )
    gen = FakeSbomGenerator()
    report = _run(_copy_policy(), registry, sbom_generator=gen, sbom_formats=["spdx-json"])

    subject = f"{dest_repo}@{mirror_digest}"
    assert gen.calls == [(subject, ("spdx-json",), True)]  # scanned the LIVE digest, no rebuild
    assert _sbom_referrers(registry, subject) == [(SPDX, SPDX)]
    ops = [op for v in report.policies[0].targets[0].variants for op in v.operations]
    assert any(op.kind == "sbom" and op.error is None for op in ops)


def test_skipped_covered_tag_is_not_resbomed() -> None:
    # SBOM referrer already on the mirror tag => sbom_covered => no generate call (converged).
    from knock.ports.registry import Referrer

    src = "docker.io/library/busybox"
    dest_repo = "reg.local/demo/busybox"
    mirror_digest = "sha256:mirrordigest"
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], dest_repo: ["1.36.0"]},
        infos={
            f"{src}:1.36.0": ImageInfo(digest="sha256:bbsrc", created=NOW, annotations={}),
            f"{dest_repo}:1.36.0": ImageInfo(
                digest=mirror_digest,
                created=NOW,
                annotations={"org.opencontainers.image.base.digest": "sha256:bbsrc"},
            ),
        },
        referrers={
            f"{dest_repo}:1.36.0": [
                Referrer(
                    digest="sha256:spdx",
                    artifact_type=SPDX,
                    annotations={},
                    subject_tag="1.36.0",
                )
            ]
        },
    )
    gen = FakeSbomGenerator()
    _run(_copy_policy(), registry, sbom_generator=gen, sbom_formats=["spdx-json"])
    assert gen.calls == []


def test_sbom_backfill_failure_is_visible() -> None:
    # Uncovered placed tag + a failing generator => a FAILED "sbom" op (no silent gap).
    src = "docker.io/library/busybox"
    dest_repo = "reg.local/demo/busybox"
    mirror_digest = "sha256:mirrordigest"
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], dest_repo: ["1.36.0"]},
        infos={
            f"{src}:1.36.0": ImageInfo(digest="sha256:bbsrc", created=NOW, annotations={}),
            f"{dest_repo}:1.36.0": ImageInfo(
                digest=mirror_digest,
                created=NOW,
                annotations={"org.opencontainers.image.base.digest": "sha256:bbsrc"},
            ),
        },
    )
    report = _run(
        _copy_policy(),
        registry,
        sbom_generator=FakeSbomGenerator(fail=True),
        sbom_formats=["spdx-json"],
    )
    ops = [op for v in report.policies[0].targets[0].variants for op in v.operations]
    sbom_ops = [op for op in ops if op.kind == "sbom"]
    assert len(sbom_ops) == 1
    assert sbom_ops[0].error is not None


def test_sbom_backfill_is_skipped_in_dry_run() -> None:
    # Dry-run: the uncovered tag is still reported as a "sbom" op, but nothing is scanned
    # or attached (applied=False) — mirrors the signature backfill's dry-run behavior.
    src = "docker.io/library/busybox"
    dest_repo = "reg.local/demo/busybox"
    mirror_digest = "sha256:mirrordigest"
    registry = FakeRegistryPort(
        tags={src: ["1.36.0"], dest_repo: ["1.36.0"]},
        infos={
            f"{src}:1.36.0": ImageInfo(digest="sha256:bbsrc", created=NOW, annotations={}),
            f"{dest_repo}:1.36.0": ImageInfo(
                digest=mirror_digest,
                created=NOW,
                annotations={"org.opencontainers.image.base.digest": "sha256:bbsrc"},
            ),
        },
    )
    gen = FakeSbomGenerator()
    report = _run(
        _copy_policy(),
        registry,
        sbom_generator=gen,
        sbom_formats=["spdx-json"],
        dry_run_tags=True,
        dry_run_deletions=True,
    )
    assert gen.calls == []  # no scan in dry-run
    assert registry.artifact_referrers == []  # nothing attached
    ops = [op for v in report.policies[0].targets[0].variants for op in v.operations]
    sbom_ops = [op for op in ops if op.kind == "sbom"]
    assert len(sbom_ops) == 1
    assert sbom_ops[0].applied is False
