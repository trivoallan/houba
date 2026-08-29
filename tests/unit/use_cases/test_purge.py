from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knock.config import RegistryConfig
from knock.domain.lifecycle import (
    PENDING_DELETION_ARTIFACT_TYPE,
    build_pending_deletion_annotations,
)
from knock.errors import ConfigError
from knock.ports.registry import ImageInfo, Referrer
from knock.use_cases.purge import purge_exit_code, purge_marks
from tests.fakes.registry import FakeRegistryPort
from tests.fakes.usage_oracle import FakeUsageOraclePort

NOW = datetime(2026, 6, 13, tzinfo=UTC)
_ROSTER = {"harbor": RegistryConfig(host="harbor.example")}


def _mark(tag: str) -> Referrer:
    return Referrer(
        digest=f"sha256:ref-{tag}",
        artifact_type=PENDING_DELETION_ARTIFACT_TYPE,
        annotations=build_pending_deletion_annotations(
            prefix="io.knock",
            marked_at=NOW,
            reason="dropped-from-selection",
            policy="redis",
            import_name="v7",
            variant="default",
        ),
        subject_tag=tag,
    )


def _registry(**kw: object) -> FakeRegistryPort:
    host = "harbor.example"
    repo = f"{host}/lib/redis"
    return FakeRegistryPort(
        repositories={host: ["lib/redis"]},
        tags={repo: ["7.1", "7.2"]},
        infos={
            f"{repo}:7.1": ImageInfo(digest="sha256:d71", created=None, annotations={}),
            f"{repo}:7.2": ImageInfo(digest="sha256:d72", created=None, annotations={}),
        },
        referrers={
            f"{repo}:7.1": [_mark("7.1")],
            f"{repo}:7.2": [_mark("7.2")],
        },
        **kw,
    )


def test_apply_purges_only_the_unused_tag_and_clears_its_mark() -> None:
    reg = _registry()
    oracle = FakeUsageOraclePort(last_seen={"sha256:d72": datetime(2026, 6, 8, tzinfo=UTC)})
    report = purge_marks(
        registry=reg,
        oracle=oracle,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        min_idle_days=15,
        now=NOW,
        apply=True,
    )
    assert reg.deleted == ["harbor.example/lib/redis:7.1"]
    assert reg.unmarked == ["harbor.example/lib/redis@sha256:ref-7.1"]
    assert {o.image_ref: o.decision for o in report.outcomes} == {
        "harbor.example/lib/redis:7.1": "purge",
        "harbor.example/lib/redis:7.2": "protect",
    }
    assert purge_exit_code(report) == 0


def test_dry_run_mutates_nothing() -> None:
    reg = _registry()
    oracle = FakeUsageOraclePort(last_seen={})
    report = purge_marks(
        registry=reg,
        oracle=oracle,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        min_idle_days=15,
        now=NOW,
        apply=False,
    )
    assert reg.deleted == []
    assert reg.unmarked == []
    assert all(o.decision == "purge" and o.applied is False for o in report.outcomes)
    assert report.mode == "dry-run"


def test_oracle_error_is_fail_closed_protect_not_purge() -> None:
    reg = _registry()
    oracle = FakeUsageOraclePort(fail={"sha256:d71", "sha256:d72"})
    report = purge_marks(
        registry=reg,
        oracle=oracle,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        min_idle_days=15,
        now=NOW,
        apply=True,
    )
    assert reg.deleted == []
    assert all(o.decision == "uncertain" for o in report.outcomes)
    assert purge_exit_code(report) == 0


def test_delete_failure_is_recorded_and_reddens_exit() -> None:
    reg = _registry(fail_delete={"harbor.example/lib/redis:7.1"})
    oracle = FakeUsageOraclePort(last_seen={})
    report = purge_marks(
        registry=reg,
        oracle=oracle,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        min_idle_days=15,
        now=NOW,
        apply=True,
    )
    errs = [o for o in report.outcomes if o.error is not None]
    assert len(errs) == 1 and errs[0].image_ref == "harbor.example/lib/redis:7.1"
    assert "harbor.example/lib/redis:7.2" in reg.deleted
    assert purge_exit_code(report) == 2


def test_unknown_only_registry_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        purge_marks(
            registry=_registry(),
            oracle=FakeUsageOraclePort(),
            roster=_ROSTER,
            only_registry="nope",
            label_prefix="io.knock",
            min_idle_days=15,
            now=NOW,
            apply=True,
        )


def test_inspect_failure_is_recorded_and_does_not_block_siblings() -> None:
    reg = _registry(fail_inspect={"harbor.example/lib/redis:7.1"})
    oracle = FakeUsageOraclePort(last_seen={})  # 7.2 unseen => would purge
    report = purge_marks(
        registry=reg,
        oracle=oracle,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        min_idle_days=15,
        now=NOW,
        apply=True,
    )
    errs = [o for o in report.outcomes if o.error is not None]
    assert [o.image_ref for o in errs] == ["harbor.example/lib/redis:7.1"]
    assert "harbor.example/lib/redis:7.2" in reg.deleted  # sibling still purged
    assert purge_exit_code(report) == 2  # RegctlError -> AdapterError -> 2


_FALLBACK_TAG = "sha256-3821e65d0f6c0d2b0a2a3f5c6e7d8a9b0c1d2e3f405162738495a6b7c8d9e0f1"


def test_referrers_fallback_tags_are_never_purged() -> None:
    # A `sha256-<digest>` tag is a referrer MANIFEST (SBOM, signature, pending-deletion mark)
    # that registries without the referrers API expose in the subject's tag list. Treating it
    # as an image is not merely wasteful: reaching the apply path would delete the referrer
    # itself. Seed it with a mark so the walk would otherwise carry it into a purge decision.
    host, repo = "harbor.example", "harbor.example/lib/redis"
    reg = FakeRegistryPort(
        repositories={host: ["lib/redis"]},
        tags={repo: ["7.1", _FALLBACK_TAG]},
        infos={
            f"{repo}:7.1": ImageInfo(digest="sha256:d71", created=None, annotations={}),
            f"{repo}:{_FALLBACK_TAG}": ImageInfo(digest="sha256:dfb", created=None, annotations={}),
        },
        referrers={
            f"{repo}:7.1": [_mark("7.1")],
            f"{repo}:{_FALLBACK_TAG}": [_mark(_FALLBACK_TAG)],
        },
    )
    report = purge_marks(
        registry=reg,
        oracle=FakeUsageOraclePort(last_seen={}),
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        min_idle_days=15,
        now=NOW,
        apply=True,
    )
    assert [o.image_ref for o in report.outcomes] == [f"{repo}:7.1"]
    assert reg.deleted == [f"{repo}:7.1"]
    assert f"{repo}:{_FALLBACK_TAG}" not in reg.deleted
