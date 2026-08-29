from __future__ import annotations

from datetime import UTC, datetime, timedelta

from knock.config import RegistryConfig
from knock.ports.registry import Referrer
from knock.use_cases.gc import gc_exit_code, gc_referrers
from tests.fakes.registry import FakeRegistryPort

NOW = datetime(2026, 6, 16, tzinfo=UTC)
PREFIX = "io.knock"
ATYPE = "application/vnd.knock.scan.result.v1"


def _ref(digest: str, *, days_old: int) -> Referrer:
    ts = NOW - timedelta(days=days_old)
    return Referrer(
        digest=digest,
        artifact_type=ATYPE,
        annotations={
            f"{PREFIX}.scan.tool": "trivy",
            f"{PREFIX}.scan.format": "sarif",
            f"{PREFIX}.scan.timestamp": ts.isoformat(),
        },
        subject_tag="lib/redis:7.2.0",
    )


def _roster() -> dict[str, RegistryConfig]:
    return {"harbor": RegistryConfig(host="harbor.example", tls_verify=False)}


def _registry() -> FakeRegistryPort:
    image = "harbor.example/lib/redis:7.2.0"
    return FakeRegistryPort(
        repositories={"harbor.example": ["lib/redis"]},
        tags={"harbor.example/lib/redis": ["7.2.0"]},
        referrers={image: [_ref("sha256:old", days_old=60), _ref("sha256:new", days_old=40)]},
    )


def test_dry_run_reports_candidates_without_deleting():
    reg = _registry()
    report = gc_referrers(
        registry=reg,
        roster=_roster(),
        only_registry=None,
        label_prefix=PREFIX,
        keep=1,
        older_than_days=30,
        now=NOW,
        apply=False,
    )
    assert report.mode == "dry-run"
    assert reg.unmarked == []  # nothing deleted
    [outcome] = report.outcomes
    assert outcome.collected == ["sha256:old"]
    assert outcome.kept == 1
    assert outcome.applied is False
    assert gc_exit_code(report) == 0


def test_apply_deletes_superseded_referrers():
    reg = _registry()
    report = gc_referrers(
        registry=reg,
        roster=_roster(),
        only_registry=None,
        label_prefix=PREFIX,
        keep=1,
        older_than_days=30,
        now=NOW,
        apply=True,
    )
    assert report.mode == "apply"
    assert reg.unmarked == ["harbor.example/lib/redis@sha256:old"]
    [outcome] = report.outcomes
    assert outcome.applied is True
    assert gc_exit_code(report) == 0


def test_only_registry_narrows_the_walk():
    reg = FakeRegistryPort(
        repositories={"harbor.example": ["lib/redis"], "other.example": ["lib/nginx"]},
        tags={"harbor.example/lib/redis": ["7.2.0"], "other.example/lib/nginx": ["1.0"]},
    )
    roster = {
        "harbor": RegistryConfig(host="harbor.example", tls_verify=False),
        "other": RegistryConfig(host="other.example", tls_verify=False),
    }
    report = gc_referrers(
        registry=reg,
        roster=roster,
        only_registry="harbor",
        label_prefix=PREFIX,
        keep=1,
        older_than_days=30,
        now=NOW,
        apply=True,
    )
    # Only the harbor repo was walked → exactly one subject outcome.
    assert [o.image_ref for o in report.outcomes] == ["harbor.example/lib/redis:7.2.0"]


def test_list_referrers_failure_reddens_exit_without_blocking_siblings():
    class Boom(FakeRegistryPort):
        def list_referrers(self, image_ref: str, artifact_type: str):
            from knock.errors import RegctlError

            raise RegctlError("boom")

    reg = Boom(
        repositories={"harbor.example": ["lib/redis"]},
        tags={"harbor.example/lib/redis": ["7.2.0"]},
    )
    report = gc_referrers(
        registry=reg,
        roster=_roster(),
        only_registry=None,
        label_prefix=PREFIX,
        keep=1,
        older_than_days=30,
        now=NOW,
        apply=True,
    )
    [outcome] = report.outcomes
    assert outcome.error is not None
    assert outcome.error.exit_code == 2  # AdapterError
    assert gc_exit_code(report) == 2


_FALLBACK_TAG = "sha256-3821e65d0f6c0d2b0a2a3f5c6e7d8a9b0c1d2e3f405162738495a6b7c8d9e0f1"


def test_referrers_fallback_tags_are_not_walked_as_subjects():
    # A `sha256-<digest>` tag IS the referrer manifest, not a subject that carries referrers:
    # on a registry without the referrers API it shows up in every `tag ls` and must not
    # become a gc subject of its own.
    reg = FakeRegistryPort(
        repositories={"harbor.example": ["lib/redis"]},
        tags={"harbor.example/lib/redis": ["7.2.0", _FALLBACK_TAG]},
        referrers={
            "harbor.example/lib/redis:7.2.0": [
                _ref("sha256:old", days_old=60),
                _ref("sha256:new", days_old=40),
            ]
        },
    )
    report = gc_referrers(
        registry=reg,
        roster=_roster(),
        only_registry=None,
        label_prefix=PREFIX,
        keep=1,
        older_than_days=30,
        now=NOW,
        apply=False,
    )
    assert [o.image_ref for o in report.outcomes] == ["harbor.example/lib/redis:7.2.0"]
