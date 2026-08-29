from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from knock.adapters.local_archiver import LocalArchiver
from knock.config import CACertSource, PackageMirror, RegistryConfig
from knock.domain.mirror_policy import MirrorPolicy, parse_mirror_policy
from knock.domain.transforms.base import ResolvedResource, ResolvedStep
from knock.domain.transforms.render import transform_version
from knock.errors import ConfigError, PolicyValidationError, RegctlError
from knock.ports.registry import ImageInfo
from knock.use_cases.reconcile import reconcile_policies
from knock.use_cases.reconcile_git import REVISION_TAG_PREFIX
from knock.use_cases.reconcile_registry import to_mirror_artifact, to_source_artifact
from knock.use_cases.report import RunReport
from tests.fakes.image_builder import FakeImageBuilder
from tests.fakes.registry import FakeRegistryPort
from tests.fakes.reporter import FakeReporter
from tests.fakes.source import FakeSourcePort

NOW = datetime(2026, 6, 11, tzinfo=UTC)
CREATED = datetime(2026, 1, 1, tzinfo=UTC)


def test_to_source_artifact_uses_created() -> None:
    art = to_source_artifact(ImageInfo("sha256:a", CREATED, {}), now=NOW)
    assert art.digest == "sha256:a"
    assert art.pushed_at == CREATED


def test_to_source_artifact_falls_back_to_now_when_created_absent() -> None:
    art = to_source_artifact(ImageInfo("sha256:a", None, {}), now=NOW)
    assert art.pushed_at == NOW


_REV = "org.opencontainers.image.revision"


def test_source_revision_from_manifest_annotation() -> None:
    info = ImageInfo("sha256:a", NOW, {_REV: "anncommit"})
    assert to_source_artifact(info, now=NOW).revision == "anncommit"


def test_source_revision_falls_back_to_config_label() -> None:
    info = ImageInfo("sha256:a", NOW, {}, {_REV: "labelcommit"})
    assert to_source_artifact(info, now=NOW).revision == "labelcommit"


def test_source_revision_annotation_wins_over_label() -> None:
    info = ImageInfo("sha256:a", NOW, {_REV: "anncommit"}, {_REV: "labelcommit"})
    assert to_source_artifact(info, now=NOW).revision == "anncommit"


def test_source_revision_absent_is_none() -> None:
    info = ImageInfo("sha256:a", NOW, {}, {})
    assert to_source_artifact(info, now=NOW).revision is None


def test_to_mirror_artifact_reads_base_digest() -> None:
    info = ImageInfo("sha256:m", CREATED, {"org.opencontainers.image.base.digest": "sha256:src"})
    art = to_mirror_artifact(info)
    assert art is not None
    assert art.base_digest == "sha256:src"


def test_to_mirror_artifact_none_when_unstamped() -> None:
    assert to_mirror_artifact(ImageInfo("sha256:m", CREATED, {})) is None


ROSTER = {"only": RegistryConfig(host="harbor.corp", username="robot", password="x")}

POLICY = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: redis }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/redis }
  imports:
    - name: v7
      owners: [group:default/platform-data]
      vendor: ACME Platform
      tags: { includeRegex: "^7\\\\.", aliases: ["{major}.{minor}", "latest"] }
      destinations: [{ project: lib, repository: redis }]
""")


def _info(digest: str, ann: dict[str, str] | None = None) -> ImageInfo:
    return ImageInfo(digest=digest, created=CREATED, annotations=ann or {})


def _run(policies, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        registry=kw.pop("registry"),
        builder=kw.pop("builder", FakeImageBuilder()),
        roster=ROSTER,
        ca_certs={},
        package_mirrors={},
        build_platform="linux/amd64",
        now=NOW,
        label_prefix="io.knock",
        dry_run_tags=kw.pop("dry_run_tags", False),
        dry_run_deletions=kw.pop("dry_run_deletions", False),
        reporter=kw.pop("reporter", FakeReporter()),
        max_concurrency=kw.pop("max_concurrency", 1),
        shard_index=kw.pop("shard_index", 0),
        shard_count=kw.pop("shard_count", 1),
        # A FakeSourcePort that resolves nothing, for the majority of these tests that
        # carry no git policy. The archiver is the real one, never a fake — see
        # `ports/archiver.py`: the defects it guards are properties of a real filesystem.
        source=kw.pop("source", FakeSourcePort()),
        archiver=kw.pop("archiver", LocalArchiver()),
        work_dir=kw.pop("work_dir", None),
    )
    return reconcile_policies(policies, **defaults)


def test_source_registry_in_roster_is_configured_before_listing() -> None:
    # Sourcing from an in-roster registry must configure its TLS (regctl set --tls disabled)
    # BEFORE the plan-phase list_tags — else a plain-HTTP / custom-CA source registry fails
    # the source `tag ls`. Source host differs from the destination host, so the config can
    # ONLY come from the source path (not the destination's apply-phase session).
    policy = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: src-insecure }
spec:
  artifactType: image
  source: { registry: src.local, repository: library/busybox }
  imports:
    - name: stable
      tags: { includeRegex: "^1\\\\.36\\\\.0$" }
      destinations: [{ registry: dst, project: demo, repository: busybox }]
""")
    registry = FakeRegistryPort(
        tags={"src.local/library/busybox": ["1.36.0"], "dst.local/demo/busybox": []},
        infos={"src.local/library/busybox:1.36.0": _info("sha256:s")},
    )
    reconcile_policies(
        [policy],
        registry=registry,
        builder=FakeImageBuilder(),
        source=FakeSourcePort(),
        archiver=LocalArchiver(),
        roster={
            "src": RegistryConfig(host="src.local", tls_verify=False),
            "dst": RegistryConfig(host="dst.local"),
        },
        ca_certs={},
        package_mirrors={},
        build_platform="linux/amd64",
        now=NOW,
        label_prefix="io.knock",
        dry_run_tags=False,
        dry_run_deletions=False,
        reporter=FakeReporter(),
    )
    assert ("src.local", False, None) in registry.configured


def test_reconcile_copies_new_tags_and_stamps() -> None:
    fake = FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0", "7.3.0"],
            "harbor.corp/lib/redis": [],
        },
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:a"),
            "docker.io/library/redis:7.3.0": _info("sha256:b"),
        },
    )
    report = _run([POLICY], registry=fake)
    assert isinstance(report, RunReport)
    assert fake.logins == [("harbor.corp", "robot", True)]
    assert ("docker.io/library/redis@sha256:a", "harbor.corp/lib/redis:7.2.0") in fake.copied
    assert ("docker.io/library/redis@sha256:b", "harbor.corp/lib/redis:7.3.0") in fake.copied
    stamped = {ref: ann for ref, ann in fake.annotated}
    pinned = "harbor.corp/lib/redis@sha256:a"
    assert stamped[pinned]["org.opencontainers.image.base.digest"] == "sha256:a"
    assert stamped[pinned]["io.knock.owners"] == "group:default/platform-data"
    assert stamped[pinned]["org.opencontainers.image.title"] == "redis"
    assert stamped[pinned]["org.opencontainers.image.vendor"] == "ACME Platform"
    assert ("harbor.corp/lib/redis:7.3.0", "harbor.corp/lib/redis:latest") in fake.copied
    assert report.totals.imported == 2


def test_namespaced_destination_carries_the_prefix_once_over_a_bare_host_session() -> None:
    # A path-prefixed roster host (`ghcr.io/acme`) splits in two: image references compose
    # from the FULL host, while regctl's registry-level commands (TLS config, login) take
    # only the bare host. Reconcile is the WRITE side of that invariant and composes its
    # destination itself, independently of the read-side catalog walk. Swapping `cfg.host`
    # for `cfg.registry_host` in that composition would publish to `ghcr.io/demo/redis` —
    # outside the namespace GHCR requires — so pin both halves here.
    policy = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: redis-namespaced }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/redis }
  imports:
    - name: v7
      tags: { includeRegex: "^7\\\\.2\\\\.0$" }
      destinations: [{ registry: ghcr, project: demo, repository: redis }]
""")
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "ghcr.io/acme/demo/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    reconcile_policies(
        [policy],
        registry=fake,
        builder=FakeImageBuilder(),
        source=FakeSourcePort(),
        archiver=LocalArchiver(),
        roster={"ghcr": RegistryConfig(host="ghcr.io/acme", username="u", password="p")},
        ca_certs={},
        package_mirrors={},
        build_platform="linux/amd64",
        now=NOW,
        label_prefix="io.knock",
        dry_run_tags=False,
        dry_run_deletions=False,
        reporter=FakeReporter(),
    )
    # The namespace appears exactly once in the destination — never doubled, never dropped.
    assert fake.copied == [("docker.io/library/redis@sha256:a", "ghcr.io/acme/demo/redis:7.2.0")]
    # …and the session behind it was established against the bare host.
    assert fake.configured == [("ghcr.io", True, None)]
    assert fake.logins == [("ghcr.io", "u", True)]


GIT_POLICY = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: example-skill }
spec:
  artifactType: skill
  source: { url: "https://github.com/example/agent-skill.git", ref: v1.2.0 }
  imports:
    - name: release
      tags: {}
      destinations: [{ project: skills, repository: example-skill }]
""")


_SKILL_URL = "https://github.com/example/agent-skill.git"
_SKILL_REF = "v1.2.0"
_SKILL_REV = "c" * 40
_SKILL_DEST = "harbor.corp/skills/example-skill"
# Seeded explicitly: FakeSourcePort materialises nothing by default, and this path drives
# the real packaging, which refuses a tree with no plugin marker.
_SKILL_TREE = {"SKILL.md": "# probe\n"}


def _skill_source(tree: dict[str, str] | None = None) -> FakeSourcePort:
    return FakeSourcePort(revisions={(_SKILL_URL, _SKILL_REF): _SKILL_REV}, tree=tree)


def test_a_git_sourced_policy_reconciles_alongside_a_registry_one(tmp_path: Path) -> None:
    # A git-sourced policy is now claimed by a planner rather than reported unsupported.
    # Asserted in the same worklist as a registry-sourced policy, because the driver's
    # job is that neither source class knows the other exists.
    fake = FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0"],
            "harbor.corp/lib/redis": [],
            _SKILL_DEST: [],
        },
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    source = _skill_source(_SKILL_TREE)
    reporter = FakeReporter()
    report = _run(
        [POLICY, GIT_POLICY], registry=fake, source=source, reporter=reporter, work_dir=tmp_path
    )

    # The registry-sourced policy reconciled fully, unaffected by its git-sourced sibling.
    assert ("docker.io/library/redis@sha256:a", "harbor.corp/lib/redis:7.2.0") in fake.copied
    assert next(p for p in report.policies if p.name == "redis").status == "ok"

    skill = next(p for p in report.policies if p.name == "example-skill")
    assert skill.status == "ok"
    assert skill.error is None
    assert (skill.totals.imported, skill.totals.aliased) == (1, 1)
    # The revision was fetched and placed under its immutable tag, with the ref name
    # copied onto it as the moving alias.
    assert source.fetched == [(_SKILL_URL, _SKILL_REF)]
    revision_tag = f"{REVISION_TAG_PREFIX}{_SKILL_REV}"
    assert [ref for ref, *_ in fake.artifacts] == [f"{_SKILL_DEST}:{revision_tag}"]
    assert (f"{_SKILL_DEST}:{revision_tag}", f"{_SKILL_DEST}:{_SKILL_REF}") in fake.copied
    assert (_SKILL_REF, "example-skill") not in [(n, e.type) for n, e in reporter.failures]
    assert (("example-skill", _SKILL_URL)) in reporter.policies_started


def test_a_rerun_over_an_unchanged_revision_places_nothing(tmp_path: Path) -> None:
    # The convergence property the whole git path exists to provide, asserted through
    # the driver: a scheduled re-run over an unchanged upstream transfers nothing.
    revision_tag = f"{REVISION_TAG_PREFIX}{_SKILL_REV}"
    fake = FakeRegistryPort(
        tags={_SKILL_DEST: [revision_tag, _SKILL_REF]},
        annotations={f"{_SKILL_DEST}:{_SKILL_REF}": {_REV: _SKILL_REV}},
    )
    source = _skill_source()
    report = _run([GIT_POLICY], registry=fake, source=source, work_dir=tmp_path)

    skill = next(p for p in report.policies if p.name == "example-skill")
    assert skill.status == "ok"
    assert skill.totals.skipped == 1
    assert (skill.totals.imported, skill.totals.aliased) == (0, 0)
    assert source.fetched == []
    assert fake.artifacts == []
    assert fake.copied == []


# Same `metadata.name` as GIT_POLICY, deliberately: a destination repository must be
# owned by exactly one policy, and `detect_dest_repo_collisions` enforces that over all
# policies before any planner runs. So the only cross-source-class alias collision that
# can reach `detect_alias_collisions` is one inside a single policy name — which is
# precisely the case no individual planner can see, and the reason the driver holds the
# check over the union of both planners' entries rather than each planner checking its own.
COLLIDING_IMAGE_POLICY = parse_mirror_policy(f"""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: {{ name: example-skill }}
spec:
  artifactType: image
  source: {{ registry: docker.io, repository: library/redis }}
  imports:
    - name: v7
      tags: {{ includeRegex: "^7\\\\.", aliases: ["{_SKILL_REF}"] }}
      destinations: [{{ project: skills, repository: example-skill }}]
""")


def test_an_alias_collision_across_source_classes_is_caught_before_any_mutation(
    tmp_path: Path,
) -> None:
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], _SKILL_DEST: []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    with pytest.raises(PolicyValidationError, match="alias collision"):
        _run(
            [COLLIDING_IMAGE_POLICY, GIT_POLICY],
            registry=fake,
            source=_skill_source(_SKILL_TREE),
            work_dir=tmp_path,
        )
    # Plan-phase, so nothing was written by either planner.
    assert fake.copied == []
    assert fake.artifacts == []
    assert fake.annotated == []


def test_copy_path_pins_the_source_digest_it_stamps() -> None:
    # TOCTOU: copying by tag lets upstream retag between the plan and the apply, so knock
    # places bytes A while stamping base.digest = B — the stamp then describes a different
    # artifact than the one placed. Pin the copy to the digest resolved during planning
    # (as the rebuild path already does) so placed bytes and stamp are the same artifact.
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    _run([POLICY], registry=fake)
    src_ref = next(s for s, d in fake.copied if d == "harbor.corp/lib/redis:7.2.0")
    ((_ref, stamp),) = fake.annotated
    stamped_digest = stamp["org.opencontainers.image.base.digest"]
    assert src_ref == f"docker.io/library/redis@{stamped_digest}"


def test_annotate_pins_the_placed_digest_not_the_mutable_tag() -> None:
    # Second half of the same TOCTOU. The copy is digest-pinned now, but annotating
    # `dest:tag` re-resolves a tag any concurrent writer can move between the copy and the
    # stamp — knock would then stamp its provenance onto bytes it never placed. Stamp the
    # digest the copy placed instead; the tag is published from the annotated result.
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    _run([POLICY], registry=fake)
    assert "harbor.corp/lib/redis@sha256:a" in [ref for ref, _ in fake.annotated]


def test_reconcile_dry_run_tags_skips_mutations() -> None:
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    report = _run([POLICY], registry=fake, dry_run_tags=True, dry_run_deletions=True)
    assert fake.copied == []
    assert fake.annotated == []
    assert fake.deleted == []
    assert report.totals.imported == 1


def test_reconcile_collision_raises_before_mutation() -> None:
    policy = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: redis }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/redis }
  imports:
    - name: a
      tags: { includeRegex: "^7\\\\.", aliases: ["latest"] }
      destinations: [{ project: lib, repository: redis }]
    - name: b
      tags: { includeRegex: "^8\\\\.", aliases: ["latest"] }
      destinations: [{ project: lib, repository: redis }]
""")
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.0.0", "8.0.0"], "harbor.corp/lib/redis": []},
        infos={
            "docker.io/library/redis:7.0.0": _info("sha256:a"),
            "docker.io/library/redis:8.0.0": _info("sha256:b"),
        },
    )
    with pytest.raises(PolicyValidationError, match="alias collision"):
        reconcile_policies(
            [policy],
            registry=fake,
            builder=FakeImageBuilder(),
            source=FakeSourcePort(),
            archiver=LocalArchiver(),
            roster=ROSTER,
            ca_certs={},
            package_mirrors={},
            build_platform="linux/amd64",
            now=NOW,
            label_prefix="io.knock",
            dry_run_tags=False,
            dry_run_deletions=False,
            reporter=FakeReporter(),
        )
    assert fake.copied == []  # fail-fast: nothing mutated


def test_reconcile_updates_changed_stable_tag() -> None:
    # mirror has 7.2.0 stamped with an OLD base.digest; source moved to a new digest,
    # source pushed long ago (past the 7-day grace) → update.
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": ["7.2.0"]},
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:NEW"),
            "harbor.corp/lib/redis:7.2.0": _info(
                "sha256:mirror", {"org.opencontainers.image.base.digest": "sha256:OLD"}
            ),
        },
    )
    report = _run([POLICY], registry=fake)
    assert report.totals.updated == 1
    assert ("docker.io/library/redis@sha256:NEW", "harbor.corp/lib/redis:7.2.0") in fake.copied
    stamped = {ref: ann for ref, ann in fake.annotated}
    assert (
        stamped["harbor.corp/lib/redis@sha256:NEW"]["org.opencontainers.image.base.digest"]
        == "sha256:NEW"
    )


def test_reconcile_idempotent_when_unchanged() -> None:
    # mirror's recorded base.digest == current source digest → skip (no cross-registry copy).
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": ["7.2.0"]},
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:same"),
            "harbor.corp/lib/redis:7.2.0": _info(
                "sha256:mirror", {"org.opencontainers.image.base.digest": "sha256:same"}
            ),
        },
    )
    report = _run([POLICY], registry=fake)
    assert report.totals.imported == 0
    assert report.totals.updated == 0
    # No cross-registry copy (source→mirror): the tag is up-to-date; idempotent.
    assert [s for s, _ in fake.copied if s.startswith("docker.io/")] == []


def test_reconcile_deletes_orphan_stamped_tag() -> None:
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": ["7.2.0", "6.0.0"]},
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:a"),
            "harbor.corp/lib/redis:7.2.0": _info(
                "sha256:m", {"org.opencontainers.image.base.digest": "sha256:a"}
            ),
            "harbor.corp/lib/redis:6.0.0": _info(
                "sha256:old", {"org.opencontainers.image.base.digest": "sha256:gone"}
            ),
        },
    )
    report = _run([POLICY], registry=fake)
    assert report.totals.deleted == 1
    assert fake.deleted == ["harbor.corp/lib/redis:6.0.0"]


def test_reconcile_does_not_delete_unstamped_tag() -> None:
    # An unstamped tag in the dest repo (no base.digest annotation) is NOT knock-managed
    # → it is invisible to reconcile and must never be deleted.
    fake = FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0"],
            "harbor.corp/lib/redis": ["7.2.0", "manual-tag"],
        },
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:a"),
            "harbor.corp/lib/redis:7.2.0": _info(
                "sha256:m", {"org.opencontainers.image.base.digest": "sha256:a"}
            ),
            "harbor.corp/lib/redis:manual-tag": _info("sha256:manual"),  # UNstamped
        },
    )
    report = _run([POLICY], registry=fake)
    assert "harbor.corp/lib/redis:manual-tag" not in fake.deleted
    assert report.totals.deleted == 0


def test_reconcile_surfaces_skipped_in_report() -> None:
    # mirror up-to-date → skip; report records a skipped operation.
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": ["7.2.0"]},
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:same"),
            "harbor.corp/lib/redis:7.2.0": _info(
                "sha256:m", {"org.opencontainers.image.base.digest": "sha256:same"}
            ),
        },
    )
    report = _run([POLICY], registry=fake)
    assert report.totals.skipped == 1
    kinds = [
        op.kind
        for p in report.policies
        for t in p.targets
        for v in t.variants
        for op in v.operations
    ]
    assert "skipped" in kinds


def test_reconcile_dry_run_marks_operations_planned() -> None:
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    report = _run([POLICY], registry=fake, dry_run_tags=True, dry_run_deletions=True)
    assert report.mode == "dry-run"
    ops = [
        op for p in report.policies for t in p.targets for v in t.variants for op in v.operations
    ]
    assert ops and all(op.applied is False for op in ops)


def test_reconcile_accumulate_and_continue_on_failure() -> None:
    # Two policies; the FIRST destination inspect fails for `boom`, the second succeeds.
    class FlakyRegistry(FakeRegistryPort):
        def inspect(self, image_ref: str):  # type: ignore[no-untyped-def]
            if image_ref == "docker.io/library/boom:1.0.0":
                raise RegctlError("inspect failed")
            return super().inspect(image_ref)

    boom = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: boom }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/boom }
  imports:
    - name: v1
      tags: { includeRegex: "^1\\\\." }
      destinations: [{ project: lib, repository: boom }]
""")
    fake = FlakyRegistry(
        tags={
            "docker.io/library/boom": ["1.0.0"],
            "docker.io/library/redis": ["7.2.0"],
            "harbor.corp/lib/boom": [],
            "harbor.corp/lib/redis": [],
        },
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    report = _run([boom, POLICY], registry=fake)
    assert report.status == "partial"
    by_name = {p.name: p for p in report.policies}
    assert by_name["boom"].status == "failed"
    assert by_name["boom"].error is not None
    assert by_name["boom"].error.type == "RegctlError"
    assert by_name["boom"].error.exit_code == 2
    assert by_name["redis"].status == "ok"
    assert by_name["redis"].totals.imported == 1


def test_reconcile_emits_events_to_reporter() -> None:
    reporter = FakeReporter()
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    _run([POLICY], registry=fake, reporter=reporter)
    assert reporter.runs_started == [(1, "apply")]
    assert ("redis", "docker.io/library/redis") in reporter.policies_started
    assert any(ev.kind == "imported" and ev.out_tag == "7.2.0" for ev in reporter.operations)
    assert len(reporter.runs_completed) == 1


def test_reconcile_delete_event_has_empty_variant() -> None:
    # Deletions are target-level (domain to_delete spans all variants), so their
    # emitted OperationEvent must carry variant="" — not a leaked last-variant name.
    reporter = FakeReporter()
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": ["7.2.0", "6.0.0"]},
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:a"),
            "harbor.corp/lib/redis:7.2.0": _info(
                "sha256:m", {"org.opencontainers.image.base.digest": "sha256:a"}
            ),
            "harbor.corp/lib/redis:6.0.0": _info(
                "sha256:old", {"org.opencontainers.image.base.digest": "sha256:gone"}
            ),
        },
    )
    _run([POLICY], registry=fake, reporter=reporter)
    delete_events = [ev for ev in reporter.operations if ev.kind == "deleted"]
    assert delete_events  # the orphan 6.0.0 tag is deleted
    assert all(ev.variant == "" for ev in delete_events)


# --- Transform / rebuild path (hardened policy) ---------------------------------

HARDENED_NOW = datetime(2026, 6, 11, tzinfo=UTC)


def _hardened_policy() -> object:
    return parse_mirror_policy(Path("docs/examples/hardened/redis.yml").read_text())


def _run_hardened(
    registry: FakeRegistryPort, builder: FakeImageBuilder, **over: object
) -> RunReport:
    kwargs: dict[str, object] = dict(
        registry=registry,
        builder=builder,
        source=FakeSourcePort(),
        archiver=LocalArchiver(),
        roster={"local": RegistryConfig(host="reg.local")},
        ca_certs={"corp": CACertSource(pem="PEMDATA")},
        package_mirrors={"corp": PackageMirror(apt="https://mirror.corp")},
        build_platform="linux/amd64",
        now=HARDENED_NOW,
        label_prefix="io.knock",
        dry_run_tags=False,
        dry_run_deletions=False,
        reporter=FakeReporter(),
    )
    kwargs.update(over)
    return reconcile_policies([_hardened_policy()], **kwargs)  # type: ignore[arg-type]


def test_transformed_variant_builds_then_stamps_lineage() -> None:
    src_repo = "docker.io/library/redis"
    registry = FakeRegistryPort(
        tags={src_repo: ["7.2.5"], "reg.local/hardened/redis": []},
        infos={
            f"{src_repo}:7.2.5": ImageInfo(
                digest="sha256:src", created=HARDENED_NOW, annotations={}
            )
        },
    )
    builder = FakeImageBuilder()
    report = _run_hardened(registry, builder)

    assert report.totals.imported == 1
    assert len(builder.requests) == 1
    req = builder.requests[0]
    assert req.image_ref == "reg.local/hardened/redis:7.2.5"
    assert req.platform == "linux/amd64"
    df = builder.dockerfiles[0]
    assert "update-ca-certificates" in df and "/etc/apt/sources.list" in df
    assert builder.contexts[0]["corp.crt"] == "PEMDATA"


def test_rebuild_propagates_dest_tls_verify_to_builder() -> None:
    # The destination registry's tls_verify must reach the build/push path, not
    # just regctl — otherwise buildkit pushes over HTTPS to a plain-HTTP registry.
    src_repo = "docker.io/library/redis"
    registry = FakeRegistryPort(
        tags={src_repo: ["7.2.5"], "reg.local/hardened/redis": []},
        infos={
            f"{src_repo}:7.2.5": ImageInfo(
                digest="sha256:src", created=HARDENED_NOW, annotations={}
            )
        },
    )
    builder = FakeImageBuilder()
    _run_hardened(
        registry, builder, roster={"local": RegistryConfig(host="reg.local", tls_verify=False)}
    )
    assert builder.requests[0].tls_verify is False
    assert registry.copied == []
    _ref, ann = registry.annotated[0]
    assert ann["io.knock.transform.steps"] == "injectCA,rewritePackageSources"
    assert ann["io.knock.transform.version"].startswith("sha256:")


def test_report_op_on_rebuild_carries_transform_steps_and_out_digest() -> None:
    src_repo = "docker.io/library/redis"
    registry = FakeRegistryPort(
        tags={src_repo: ["7.2.5"], "reg.local/hardened/redis": []},
        infos={
            f"{src_repo}:7.2.5": ImageInfo(
                digest="sha256:src", created=HARDENED_NOW, annotations={}
            )
        },
    )
    report = _run_hardened(registry, FakeImageBuilder())
    [op] = report.policies[0].targets[0].variants[0].operations
    assert op.kind == "imported"
    # the applied transform steps make a rebuild distinguishable from a copy
    assert op.transform_steps == ["injectCA", "rewritePackageSources"]
    # the produced (post-annotate) digest, distinct from the source/base digest
    assert op.out_digest is not None and op.out_digest.startswith("sha256:")
    assert op.digest == "sha256:src"


def test_report_op_on_copy_has_no_transform_steps_but_records_out_digest() -> None:
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    report = _run([POLICY], registry=fake)
    imported = [
        o for o in report.policies[0].targets[0].variants[0].operations if o.kind == "imported"
    ]
    assert imported
    for o in imported:
        assert o.transform_steps is None
        assert o.out_digest is not None and o.out_digest.startswith("sha256:")


def test_transformed_variant_skips_when_version_matches() -> None:
    src_repo = "docker.io/library/redis"
    steps = (
        parse_mirror_policy(Path("docs/examples/hardened/redis.yml").read_text())
        .spec.imports[0]
        .transform
    )
    resolved_steps = [
        ResolvedStep(
            steps[0],  # injectCA: certs ["corp"]
            (ResolvedResource(kind="caCert", name="corp", filename="corp.crt", content="PEMDATA"),),
        ),
        ResolvedStep(
            steps[1],  # rewritePackageSources: mirror "corp"
            (ResolvedResource(kind="packageMirror", name="corp", apt="https://mirror.corp"),),
        ),
    ]
    tv = transform_version(resolved_steps)

    registry = FakeRegistryPort(
        tags={src_repo: ["7.2.5"], "reg.local/hardened/redis": ["7.2.5"]},
        infos={
            f"{src_repo}:7.2.5": ImageInfo(
                digest="sha256:src", created=HARDENED_NOW, annotations={}
            ),
            "reg.local/hardened/redis:7.2.5": ImageInfo(
                digest="sha256:built",
                created=HARDENED_NOW,
                annotations={
                    "org.opencontainers.image.base.digest": "sha256:src",
                    "io.knock.transform.version": tv,
                },
            ),
        },
    )
    builder = FakeImageBuilder()
    report = _run_hardened(registry, builder)
    assert report.totals.imported == 0 and report.totals.updated == 0
    assert builder.requests == []


def test_unknown_cert_name_raises_config_error() -> None:
    src_repo = "docker.io/library/redis"
    registry = FakeRegistryPort(
        tags={src_repo: ["7.2.5"], "reg.local/hardened/redis": []},
        infos={
            f"{src_repo}:7.2.5": ImageInfo(
                digest="sha256:src", created=HARDENED_NOW, annotations={}
            )
        },
    )
    with pytest.raises(ConfigError, match="unknown CA cert"):
        _run_hardened(registry, FakeImageBuilder(), ca_certs={})


def test_build_stages_under_work_dir(tmp_path: Path) -> None:
    src_repo = "docker.io/library/redis"
    registry = FakeRegistryPort(
        tags={src_repo: ["7.2.5"], "reg.local/hardened/redis": []},
        infos={
            f"{src_repo}:7.2.5": ImageInfo(
                digest="sha256:src", created=HARDENED_NOW, annotations={}
            )
        },
    )
    builder = FakeImageBuilder()
    wd = tmp_path / "knock-work"
    assert not wd.exists()
    _run_hardened(registry, builder, work_dir=wd)
    assert wd.exists()  # _build_variant created the configured work_dir
    assert len(builder.requests) == 1


def test_missing_cert_file_raises_config_error_before_mutation() -> None:
    src_repo = "docker.io/library/redis"
    registry = FakeRegistryPort(
        tags={src_repo: ["7.2.5"], "reg.local/hardened/redis": []},
        infos={
            f"{src_repo}:7.2.5": ImageInfo(
                digest="sha256:src", created=HARDENED_NOW, annotations={}
            )
        },
    )
    builder = FakeImageBuilder()
    with pytest.raises(ConfigError, match="cannot read CA cert file"):
        _run_hardened(registry, builder, ca_certs={"corp": CACertSource(path="/no/such/cert.pem")})
    assert registry.copied == [] and registry.deleted == [] and builder.requests == []


def _copy_policy() -> MirrorPolicy:
    return parse_mirror_policy(
        """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: busybox-copy
spec:
  artifactType: image
  source:
    registry: docker.io
    repository: library/busybox
  imports:
    - name: stable
      tags:
        includeRegex: "^1\\\\.36\\\\.0$"
      destinations:
        - project: demo
          repository: busybox
"""
    )


def test_configure_registry_called_once_per_host_before_copy() -> None:
    src_repo = "docker.io/library/busybox"
    registry = FakeRegistryPort(
        tags={src_repo: ["1.36.0"], "reg.local/demo/busybox": []},
        infos={
            f"{src_repo}:1.36.0": ImageInfo(digest="sha256:s", created=HARDENED_NOW, annotations={})
        },
    )
    builder = FakeImageBuilder()
    reconcile_policies(
        [_copy_policy()],
        registry=registry,
        builder=builder,
        source=FakeSourcePort(),
        archiver=LocalArchiver(),
        roster={"local": RegistryConfig(host="reg.local", tls_verify=False, ca_cert="/ca.pem")},
        ca_certs={},
        package_mirrors={},
        build_platform="linux/amd64",
        now=HARDENED_NOW,
        label_prefix="io.knock",
        dry_run_tags=False,
        dry_run_deletions=False,
        reporter=FakeReporter(),
    )
    assert registry.configured == [("reg.local", False, "/ca.pem")]


def test_reconcile_collects_partial_tag_failure() -> None:
    fake = FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0", "7.3.0"],
            "harbor.corp/lib/redis": [],
        },
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:a"),
            "docker.io/library/redis:7.3.0": _info("sha256:b"),
        },
        fail_copy={"harbor.corp/lib/redis:7.3.0"},
    )
    report = _run([POLICY], registry=fake)
    assert report.status == "partial"
    policy = report.policies[0]
    assert policy.status == "partial"
    assert policy.totals.imported == 1
    assert policy.totals.failed == 1
    assert ("docker.io/library/redis@sha256:a", "harbor.corp/lib/redis:7.2.0") in fake.copied
    ops = [op for t in policy.targets for v in t.variants for op in v.operations]
    failed = [op for op in ops if op.error is not None]
    assert [op.out_tag for op in failed] == ["7.3.0"]
    assert failed[0].error.type == "RegctlError"
    from knock.use_cases.report import report_exit_code

    assert report_exit_code(report) == 2


def test_reconcile_collects_alias_failure() -> None:
    # POLICY aliases 7.2.0 to "7.2" and "latest"; fail the "latest" alias copy.
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
        fail_copy={"harbor.corp/lib/redis:latest"},
    )
    report = _run([POLICY], registry=fake)
    assert report.status == "partial"
    ops = [op for t in report.policies[0].targets for v in t.variants for op in v.operations]
    aliased_failed = [op for op in ops if op.kind == "aliased" and op.error is not None]
    assert [op.out_tag for op in aliased_failed] == ["latest"]
    assert report.policies[0].totals.imported == 1
    assert report.policies[0].totals.failed == 1
    assert ("harbor.corp/lib/redis:7.2.0", "harbor.corp/lib/redis:7.2") in fake.copied


CONCURRENCY_POLICY = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: redis }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/redis }
  imports:
    - name: v7
      tags: { includeRegex: "^7\\\\." }
      destinations: [{ project: lib, repository: redis }]
""")


def _three_tag_registry(**kw):  # type: ignore[no-untyped-def]
    return FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.0.0", "7.1.0", "7.2.0"],
            "harbor.corp/lib/redis": [],
        },
        infos={
            "docker.io/library/redis:7.0.0": _info("sha256:a"),
            "docker.io/library/redis:7.1.0": _info("sha256:b"),
            "docker.io/library/redis:7.2.0": _info("sha256:c"),
        },
        **kw,
    )


def test_reconcile_imports_run_concurrently() -> None:
    import threading

    # A barrier of 3 only releases when all three imports are in `copy` at once.
    # Under sequential execution the first copy would block forever (timeout) and
    # raise BrokenBarrierError, surfacing as failed ops.
    barrier = threading.Barrier(3, timeout=5)
    fake = _three_tag_registry(copy_barrier=barrier)
    report = _run([CONCURRENCY_POLICY], registry=fake, max_concurrency=3)
    assert report.status == "ok"
    assert report.totals.imported == 3
    assert len(fake.copied) == 3


def test_reconcile_report_is_deterministic_under_concurrency() -> None:
    fake = _three_tag_registry()
    report = _run([CONCURRENCY_POLICY], registry=fake, max_concurrency=3)
    variant = report.policies[0].targets[0].variants[0]
    # Operations keep input (selection) order regardless of completion order.
    assert [op.out_tag for op in variant.operations] == ["7.0.0", "7.1.0", "7.2.0"]


def test_reconcile_sequential_and_concurrent_agree() -> None:
    seq = _run([CONCURRENCY_POLICY], registry=_three_tag_registry(), max_concurrency=1)
    par = _run([CONCURRENCY_POLICY], registry=_three_tag_registry(), max_concurrency=3)
    assert seq.model_dump() == par.model_dump()


SECOND_POLICY = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: nginx }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/nginx }
  imports:
    - name: v1
      tags: { includeRegex: "^1\\\\." }
      destinations: [{ project: lib, repository: nginx }]
""")


def _two_policy_registry() -> FakeRegistryPort:
    return FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0"],
            "docker.io/library/nginx": ["1.25.0"],
            "harbor.corp/lib/redis": [],
            "harbor.corp/lib/nginx": [],
        },
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:a"),
            "docker.io/library/nginx:1.25.0": _info("sha256:b"),
        },
    )


def test_shard_filter_applies_only_owned_policies() -> None:
    from knock.domain.sharding import owns

    policies = [POLICY, SECOND_POLICY]
    owned0 = {
        p.metadata.name for p in policies if owns(p.metadata.name, shard_index=0, shard_count=2)
    }
    report = _run(policies, registry=_two_policy_registry(), shard_index=0, shard_count=2)
    assert {p.name for p in report.policies} == owned0


def test_shards_partition_the_policy_set() -> None:
    policies = [POLICY, SECOND_POLICY]
    r0 = _run(policies, registry=_two_policy_registry(), shard_index=0, shard_count=2)
    r1 = _run(policies, registry=_two_policy_registry(), shard_index=1, shard_count=2)
    seen = {p.name for p in r0.policies} | {p.name for p in r1.policies}
    assert seen == {"redis", "nginx"}
    assert not ({p.name for p in r0.policies} & {p.name for p in r1.policies})


def test_dest_repo_collision_fails_before_any_mutation() -> None:
    clash = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: redis-clone }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/redis }
  imports:
    - name: v7
      tags: { includeRegex: "^7\\\\." }
      destinations: [{ project: lib, repository: redis }]
""")
    fake = FakeRegistryPort(
        tags={"docker.io/library/redis": ["7.2.0"], "harbor.corp/lib/redis": []},
        infos={"docker.io/library/redis:7.2.0": _info("sha256:a")},
    )
    with pytest.raises(PolicyValidationError, match=r"harbor\.corp/lib/redis"):
        _run([POLICY, clash], registry=fake)  # both → harbor.corp/lib/redis
    assert fake.copied == []  # invariant ran before any mutation


def test_shard_count_one_processes_all() -> None:
    report = _run([POLICY, SECOND_POLICY], registry=_two_policy_registry())
    assert {p.name for p in report.policies} == {"redis", "nginx"}


# --- Revision propagation tests --------------------------------------------------

_OCI_REVISION = "org.opencontainers.image.revision"


def test_stamp_propagates_upstream_revision() -> None:
    # Source image declares .revision as a manifest annotation; the mirrored stamp carries it.
    fake = FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0"],
            "harbor.corp/lib/redis": [],
        },
        infos={
            "docker.io/library/redis:7.2.0": ImageInfo(
                digest="sha256:a",
                created=CREATED,
                annotations={_OCI_REVISION: "upstreamcommit"},
            ),
        },
    )
    _run([POLICY], registry=fake)
    dest_anns = [ann for ref, ann in fake.annotated]
    assert dest_anns
    assert dest_anns[0][_OCI_REVISION] == "upstreamcommit"


def test_stamp_omits_revision_when_source_has_none() -> None:
    # Source declares no .revision (annotations={}, config_labels default {}).
    fake = FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0"],
            "harbor.corp/lib/redis": [],
        },
        infos={
            "docker.io/library/redis:7.2.0": ImageInfo(
                digest="sha256:a",
                created=CREATED,
                annotations={},
            ),
        },
    )
    _run([POLICY], registry=fake)
    dest_anns = [ann for ref, ann in fake.annotated]
    assert dest_anns
    assert _OCI_REVISION not in dest_anns[0]


_FALLBACK_TAG = "sha256-3821e65d0f6c0d2b0a2a3f5c6e7d8a9b0c1d2e3f405162738495a6b7c8d9e0f1"


def test_destination_walk_skips_referrers_fallback_tags() -> None:
    # Registries without the referrers API (GHCR, older Harbor/ECR) store referrer manifests
    # under a `sha256-<digest>` tag in the SUBJECT's repo, so knock's own first run seeds them
    # into the destination tag list. They are not images — `inspect` fails on them ("platform
    # not found") — which made a second reconcile into such a registry impossible.
    # `infos` is seeded ONLY for the real tag: reaching the fallback tag raises from the fake.
    fake = FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0"],
            "harbor.corp/lib/redis": ["7.2.0", _FALLBACK_TAG],
        },
        infos={
            "docker.io/library/redis:7.2.0": _info("sha256:same"),
            "harbor.corp/lib/redis:7.2.0": _info(
                "sha256:mirror", {"org.opencontainers.image.base.digest": "sha256:same"}
            ),
        },
    )
    report = _run([POLICY], registry=fake)
    # The real tag is up-to-date → idempotent, and the fallback tag is neither an orphan
    # to delete nor an image to inspect.
    assert report.status == "ok"
    assert report.totals.imported == 0
    assert report.totals.updated == 0
    assert report.totals.deleted == 0
    assert fake.deleted == []


def test_source_listing_skips_referrers_fallback_tags() -> None:
    # Same schema on the SOURCE side: a `sha256-<digest>` tag is never an image a policy
    # means to mirror, so it must not survive into selection (here `semverOnly: false`
    # would otherwise let it through).
    policy = parse_mirror_policy("""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: busybox }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/busybox }
  imports:
    - name: all
      tags: { semverOnly: false }
      destinations: [{ project: lib, repository: busybox }]
""")
    fake = FakeRegistryPort(
        tags={
            "docker.io/library/busybox": ["1.38.0", _FALLBACK_TAG],
            "harbor.corp/lib/busybox": [],
        },
        infos={"docker.io/library/busybox:1.38.0": _info("sha256:a")},
    )
    report = _run([policy], registry=fake)
    assert report.status == "ok"
    assert fake.copied == [("docker.io/library/busybox@sha256:a", "harbor.corp/lib/busybox:1.38.0")]
    assert report.totals.imported == 1
