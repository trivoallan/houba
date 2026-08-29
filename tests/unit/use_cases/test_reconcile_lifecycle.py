from datetime import UTC, datetime, timedelta

from knock.adapters.local_archiver import LocalArchiver
from knock.config import RegistryConfig
from knock.domain.deletion_mode import DeletionMode
from knock.domain.lifecycle import PENDING_DELETION_ARTIFACT_TYPE
from knock.domain.mirror_policy import Archive, parse_mirror_policy
from knock.ports.registry import ImageInfo, Referrer
from knock.use_cases.reconcile import reconcile_policies
from knock.use_cases.report import RunReport
from tests.fakes.image_builder import FakeImageBuilder
from tests.fakes.registry import FakeRegistryPort
from tests.fakes.reporter import FakeReporter
from tests.fakes.source import FakeSourcePort

_NOW = datetime(2026, 6, 12, tzinfo=UTC)

_POLICY = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
spec:
  artifactType: image
  source:
    registry: docker.io
    repository: library/redis
  deletionMode: {mode}
  imports:
    - name: stable
      tags:
        includeRegex: "^7\\\\."
      destinations:
        - registry: harbor
          project: lib
          repository: redis
"""


def _roster() -> dict[str, RegistryConfig]:
    return {"harbor": RegistryConfig(host="harbor.corp")}


def _seed_registry(referrers: dict[str, list[Referrer]] | None = None) -> FakeRegistryPort:
    # source has 7.2.0; mirror has the desired 7.2.0 (stamped) plus obsolete 6.0.0 (stamped)
    stamped = {"org.opencontainers.image.base.digest": "sha256:s"}
    return FakeRegistryPort(
        tags={
            "docker.io/library/redis": ["7.2.0"],
            "harbor.corp/lib/redis": ["7.2.0", "6.0.0"],
        },
        infos={
            "docker.io/library/redis:7.2.0": ImageInfo(
                digest="sha256:s", created=_NOW, annotations={}
            ),
            "harbor.corp/lib/redis:7.2.0": ImageInfo(
                digest="sha256:s", created=_NOW, annotations=stamped
            ),
            "harbor.corp/lib/redis:6.0.0": ImageInfo(
                digest="sha256:old", created=_NOW, annotations=stamped
            ),
        },
        referrers=referrers,
    )


def _run(reg: FakeRegistryPort, mode: str, *, global_mode: DeletionMode) -> RunReport:
    return reconcile_policies(
        [parse_mirror_policy(_POLICY.format(mode=mode))],
        registry=reg,
        builder=FakeImageBuilder(),
        source=FakeSourcePort(),
        archiver=LocalArchiver(),
        roster=_roster(),
        ca_certs={},
        package_mirrors={},
        build_platform="linux/amd64",
        now=_NOW,
        label_prefix="io.knock",
        dry_run_tags=False,
        dry_run_deletions=False,
        reporter=FakeReporter(),
        deletion_mode=global_mode,
    )


def test_mark_mode_marks_obsolete_tag_and_does_not_delete() -> None:
    reg = _seed_registry()
    _run(reg, "mark", global_mode=DeletionMode.purge)  # policy 'mark' overrides global 'purge'
    assert reg.deleted == []
    assert [m[0] for m in reg.marked] == ["harbor.corp/lib/redis:6.0.0"]
    assert reg.marked[0][1] == PENDING_DELETION_ARTIFACT_TYPE
    assert reg.marked[0][2]["io.knock.lifecycle.state"] == "pending-deletion"


def test_mark_mode_is_idempotent_for_already_marked_tag() -> None:
    existing = {
        "harbor.corp/lib/redis:6.0.0": [
            Referrer(
                digest="sha256:ref1",
                artifact_type=PENDING_DELETION_ARTIFACT_TYPE,
                annotations={},
                subject_tag="harbor.corp/lib/redis:6.0.0",
            )
        ]
    }
    reg = _seed_registry(referrers=existing)
    _run(reg, "mark", global_mode=DeletionMode.purge)
    assert reg.marked == []  # already marked → not re-marked
    assert reg.deleted == []


def test_purge_mode_deletes_obsolete_tag() -> None:
    reg = _seed_registry()
    _run(reg, "purge", global_mode=DeletionMode.mark)  # policy 'purge' overrides global 'mark'
    assert reg.deleted == ["harbor.corp/lib/redis:6.0.0"]
    assert reg.marked == []


def test_re_entered_tag_is_unmarked() -> None:
    # 7.2.0 is still desired but carries a stale pending-deletion mark → auto-unmark.
    existing = {
        "harbor.corp/lib/redis:7.2.0": [
            Referrer(
                digest="sha256:ref7",
                artifact_type=PENDING_DELETION_ARTIFACT_TYPE,
                annotations={},
                subject_tag="harbor.corp/lib/redis:7.2.0",
            )
        ]
    }
    reg = _seed_registry(referrers=existing)
    _run(reg, "mark", global_mode=DeletionMode.mark)
    assert reg.unmarked == ["harbor.corp/lib/redis@sha256:ref7"]
    # 6.0.0 (still obsolete) still gets marked; 7.2.0 only gets unmarked.
    assert [m[0] for m in reg.marked] == ["harbor.corp/lib/redis:6.0.0"]


def test_mark_failure_is_reported() -> None:
    reg = _seed_registry()
    reg._fail_put.add("harbor.corp/lib/redis:6.0.0")
    report = _run(reg, "mark", global_mode=DeletionMode.purge)
    assert reg.marked == []  # put_referrer raised before journaling
    assert report.totals.failed == 1
    assert report.totals.marked == 0  # a failed mark is not counted as marked
    assert report.status == "partial"  # the 7.2.0 import succeeded; the mark failed


_RETENTION_POLICY = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
spec:
  artifactType: image
  source:
    registry: docker.io
    repository: library/redis
  imports:
    - name: stable
      tags:
        includeRegex: "^7\\\\."
      archive:
        keep: 2
        olderThanDays: 30
      destinations:
        - registry: harbor
          project: lib
          repository: redis
"""

_PLAIN_POLICY = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
spec:
  artifactType: image
  source:
    registry: docker.io
    repository: library/redis
  imports:
    - name: stable
      tags:
        includeRegex: "^7\\\\."
      destinations:
        - registry: harbor
          project: lib
          repository: redis
"""


def _stamped(days_ago: int) -> dict[str, str]:
    return {
        "org.opencontainers.image.base.digest": "sha256:s",
        "org.opencontainers.image.created": (_NOW - timedelta(days=days_ago)).isoformat(),
    }


def _seed_retention(referrers: dict[str, list[Referrer]] | None = None) -> FakeRegistryPort:
    tags = ["7.2.0", "7.2.1", "7.2.2", "7.2.3"]
    ages = {"7.2.0": 60, "7.2.1": 50, "7.2.2": 2, "7.2.3": 1}
    infos = {
        f"docker.io/library/redis:{t}": ImageInfo(digest="sha256:s", created=_NOW, annotations={})
        for t in tags
    }
    infos.update(
        {
            f"harbor.corp/lib/redis:{t}": ImageInfo(
                digest="sha256:s", created=_NOW, annotations=_stamped(ages[t])
            )
            for t in tags
        }
    )
    return FakeRegistryPort(
        tags={"docker.io/library/redis": tags, "harbor.corp/lib/redis": tags},
        infos=infos,
        referrers=referrers,
    )


def _run_retention(
    reg: FakeRegistryPort,
    policy: str,
    *,
    dry_run_deletions: bool = False,
    retention_global: Archive | None = None,
) -> RunReport:
    return reconcile_policies(
        [parse_mirror_policy(policy)],
        registry=reg,
        builder=FakeImageBuilder(),
        source=FakeSourcePort(),
        archiver=LocalArchiver(),
        roster=_roster(),
        ca_certs={},
        package_mirrors={},
        build_platform="linux/amd64",
        now=_NOW,
        label_prefix="io.knock",
        dry_run_tags=False,
        dry_run_deletions=dry_run_deletions,
        reporter=FakeReporter(),
        deletion_mode=DeletionMode.purge,
        retention_global=retention_global,
    )


def test_retention_marks_old_excess_with_reason_and_never_deletes() -> None:
    reg = _seed_retention()
    _run_retention(reg, _RETENTION_POLICY)
    marked = sorted(m[0] for m in reg.marked)
    assert marked == ["harbor.corp/lib/redis:7.2.0", "harbor.corp/lib/redis:7.2.1"]
    by_tag = {m[0]: m[2] for m in reg.marked}
    assert by_tag["harbor.corp/lib/redis:7.2.0"]["io.knock.lifecycle.reason"] == "retention-excess"
    assert reg.deleted == []


def test_retention_is_suppressed_by_dry_run_deletions() -> None:
    reg = _seed_retention()
    _run_retention(reg, _RETENTION_POLICY, dry_run_deletions=True)
    assert reg.marked == []


def test_retention_global_cascade_applies_when_policy_has_no_archive() -> None:
    reg = _seed_retention()
    _run_retention(reg, _PLAIN_POLICY, retention_global=Archive(keep=2, older_than_days=30))
    marked = sorted(m[0] for m in reg.marked)
    assert marked == ["harbor.corp/lib/redis:7.2.0", "harbor.corp/lib/redis:7.2.1"]


def test_retention_unmarks_a_tag_no_longer_excess() -> None:
    existing = {
        "harbor.corp/lib/redis:7.2.3": [
            Referrer(
                digest="sha256:refR",
                artifact_type=PENDING_DELETION_ARTIFACT_TYPE,
                annotations={"io.knock.lifecycle.reason": "retention-excess"},
                subject_tag="harbor.corp/lib/redis:7.2.3",
            )
        ]
    }
    reg = _seed_retention(referrers=existing)
    _run_retention(reg, _RETENTION_POLICY)
    assert "harbor.corp/lib/redis@sha256:refR" in reg.unmarked


def test_retention_keeps_existing_mark_when_still_excess() -> None:
    existing = {
        "harbor.corp/lib/redis:7.2.0": [
            Referrer(
                digest="sha256:ref0",
                artifact_type=PENDING_DELETION_ARTIFACT_TYPE,
                annotations={"io.knock.lifecycle.reason": "retention-excess"},
                subject_tag="harbor.corp/lib/redis:7.2.0",
            )
        ]
    }
    reg = _seed_retention(referrers=existing)
    _run_retention(reg, _RETENTION_POLICY)
    marked = [m[0] for m in reg.marked]
    # 7.2.0 is still excess and already retention-marked -> not re-marked, not unmarked
    assert "harbor.corp/lib/redis:7.2.0" not in marked
    assert "harbor.corp/lib/redis@sha256:ref0" not in reg.unmarked
    # 7.2.1 (excess, not yet marked) still gets a fresh mark
    assert "harbor.corp/lib/redis:7.2.1" in marked


def test_unmark_is_axis_scoped() -> None:
    # 7.2.0 (oldest, still retention-excess) carries BOTH a stale selection mark and a
    # retention mark. Selection re-entry must clear ONLY the selection referrer; the
    # still-excess retention referrer must survive.
    both = {
        "harbor.corp/lib/redis:7.2.0": [
            Referrer(
                digest="sha256:refS",
                artifact_type=PENDING_DELETION_ARTIFACT_TYPE,
                annotations={"io.knock.lifecycle.reason": "dropped-from-selection"},
                subject_tag="harbor.corp/lib/redis:7.2.0",
            ),
            Referrer(
                digest="sha256:refR",
                artifact_type=PENDING_DELETION_ARTIFACT_TYPE,
                annotations={"io.knock.lifecycle.reason": "retention-excess"},
                subject_tag="harbor.corp/lib/redis:7.2.0",
            ),
        ]
    }
    reg = _seed_retention(referrers=both)
    _run_retention(reg, _RETENTION_POLICY)
    assert "harbor.corp/lib/redis@sha256:refS" in reg.unmarked  # selection mark cleared
    assert "harbor.corp/lib/redis@sha256:refR" not in reg.unmarked  # retention mark survives
