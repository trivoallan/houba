from __future__ import annotations

import json

import pytest

from knock.config import RegistryConfig
from knock.domain.attestation import COSIGN_ATTESTATION_ARTIFACT_TYPE
from knock.errors import ConfigError
from knock.ports.registry import Referrer
from knock.use_cases.audit import (
    audit_coverage,
    audit_exit_code,
    coverage_report_json_schema,
)
from tests.fakes.registry import FakeRegistryPort

_ROSTER = {"harbor": RegistryConfig(host="harbor.example")}
_HOST = "harbor.example"
_REPO = f"{_HOST}/lib/redis"


def _cosign_ref(subject: str) -> Referrer:
    return Referrer(
        digest="sha256:sig",
        artifact_type=COSIGN_ATTESTATION_ARTIFACT_TYPE,
        annotations={},
        subject_tag=subject,
    )


def _reg(**kw: object) -> FakeRegistryPort:
    return FakeRegistryPort(
        repositories={_HOST: ["lib/redis"]},
        tags={_REPO: ["7.1", "7.2"]},
        annotations={
            f"{_REPO}:7.1": {"io.knock.policy": "redis"},  # covered
            f"{_REPO}:7.2": {},  # uncovered
        },
        **kw,
    )


def test_reports_covered_and_uncovered() -> None:
    report = audit_coverage(
        registry=_reg(), roster=_ROSTER, only_registry=None, label_prefix="io.knock"
    )
    by = {o.image_ref: o for o in report.outcomes}
    assert by[f"{_REPO}:7.1"].covered is True
    assert by[f"{_REPO}:7.1"].policy == "redis"
    assert by[f"{_REPO}:7.2"].covered is False
    assert by[f"{_REPO}:7.2"].policy is None
    assert report.counts.scanned == 2
    assert report.counts.covered == 1
    assert report.counts.uncovered == 1
    assert report.counts.errored == 0
    assert audit_exit_code(report, fail_on_uncovered=False) == 0
    assert audit_exit_code(report, fail_on_uncovered=True) == 1


def test_all_covered_passes_the_gate() -> None:
    reg = FakeRegistryPort(
        repositories={_HOST: ["lib/redis"]},
        tags={_REPO: ["7.1"]},
        annotations={f"{_REPO}:7.1": {"io.knock.policy": "redis"}},
    )
    report = audit_coverage(
        registry=reg, roster=_ROSTER, only_registry=None, label_prefix="io.knock"
    )
    assert report.counts.uncovered == 0
    assert audit_exit_code(report, fail_on_uncovered=True) == 0


def test_read_error_recorded_and_reddens_exit_without_blocking_siblings() -> None:
    reg = _reg(fail_get={f"{_REPO}:7.1"})
    report = audit_coverage(
        registry=reg, roster=_ROSTER, only_registry=None, label_prefix="io.knock"
    )
    errs = [o for o in report.outcomes if o.error is not None]
    assert [o.image_ref for o in errs] == [f"{_REPO}:7.1"]
    assert errs[0].error is not None and errs[0].error.type == "RegctlError"
    assert report.counts.errored == 1
    assert any(o.image_ref == f"{_REPO}:7.2" and not o.covered for o in report.outcomes)
    assert audit_exit_code(report, fail_on_uncovered=False) == 2  # AdapterError -> 2


def test_only_registry_restricts_to_one_host() -> None:
    report = audit_coverage(
        registry=_reg(), roster=_ROSTER, only_registry="harbor", label_prefix="io.knock"
    )
    assert report.registries == ["harbor.example"]


def test_unknown_only_registry_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        audit_coverage(
            registry=_reg(), roster=_ROSTER, only_registry="nope", label_prefix="io.knock"
        )


def test_configure_and_login_once_per_host() -> None:
    reg = _reg()
    roster = {"harbor": RegistryConfig(host=_HOST, username="robot", password="s3cret")}
    audit_coverage(registry=reg, roster=roster, only_registry=None, label_prefix="io.knock")
    assert reg.configured == [(_HOST, True, None)]
    assert reg.logins == [(_HOST, "robot", True)]


def test_json_schema_is_stable_and_serializable() -> None:
    json.dumps(coverage_report_json_schema())


def test_check_signed_off_leaves_signed_none() -> None:
    report = audit_coverage(
        registry=_reg(), roster=_ROSTER, only_registry=None, label_prefix="io.knock"
    )
    assert all(o.signed is None for o in report.outcomes)
    assert report.counts.signed == 0
    assert report.counts.unsigned == 0


def test_check_signed_probes_only_covered_images() -> None:
    reg = _reg(referrers={f"{_REPO}:7.1": [_cosign_ref(f"{_REPO}:7.1")]})
    report = audit_coverage(
        registry=reg,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        check_signed=True,
    )
    by = {o.image_ref: o for o in report.outcomes}
    assert by[f"{_REPO}:7.1"].signed is True  # covered + has cosign referrer
    assert by[f"{_REPO}:7.2"].signed is None  # uncovered -> not probed
    assert report.counts.signed == 1
    assert report.counts.unsigned == 0


def test_check_signed_covered_without_referrer_is_unsigned() -> None:
    report = audit_coverage(
        registry=_reg(),  # no referrers seeded
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        check_signed=True,
    )
    by = {o.image_ref: o for o in report.outcomes}
    assert by[f"{_REPO}:7.1"].signed is False
    assert report.counts.signed == 0
    assert report.counts.unsigned == 1


def test_fail_on_unsigned_gate() -> None:
    report = audit_coverage(
        registry=_reg(),  # 7.1 covered-but-unsigned, 7.2 uncovered
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        check_signed=True,
    )
    assert report.counts.unsigned == 1
    assert audit_exit_code(report, fail_on_uncovered=False, fail_on_unsigned=False) == 0
    assert audit_exit_code(report, fail_on_uncovered=False, fail_on_unsigned=True) == 1


def test_read_error_dominates_unsigned_gate() -> None:
    reg = _reg(fail_get={f"{_REPO}:7.1"})
    report = audit_coverage(
        registry=reg,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        check_signed=True,
    )
    # AdapterError -> 2 wins over the unsigned gate's 1
    assert audit_exit_code(report, fail_on_uncovered=False, fail_on_unsigned=True) == 2


def test_outcomes_carry_the_digest() -> None:
    reg = _reg(digests={f"{_REPO}:7.1": "sha256:d71", f"{_REPO}:7.2": "sha256:d72"})
    report = audit_coverage(
        registry=reg, roster=_ROSTER, only_registry=None, label_prefix="io.knock"
    )
    by = {o.image_ref: o for o in report.outcomes}
    assert by[f"{_REPO}:7.1"].digest == "sha256:d71"  # covered carries it
    assert by[f"{_REPO}:7.2"].digest == "sha256:d72"  # uncovered carries it too


def test_read_error_leaves_digest_none() -> None:
    reg = _reg(fail_get={f"{_REPO}:7.1"})
    report = audit_coverage(
        registry=reg, roster=_ROSTER, only_registry=None, label_prefix="io.knock"
    )
    by = {o.image_ref: o for o in report.outcomes}
    assert by[f"{_REPO}:7.1"].error is not None
    assert by[f"{_REPO}:7.1"].digest is None


def _sbom_ref(subject: str, fmt: str = "application/spdx+json") -> Referrer:
    return Referrer(digest="sha256:sbom", artifact_type=fmt, annotations={}, subject_tag=subject)


def test_check_sbom_off_leaves_sbom_none() -> None:
    report = audit_coverage(
        registry=_reg(), roster=_ROSTER, only_registry=None, label_prefix="io.knock"
    )
    assert all(o.sbom is None for o in report.outcomes)
    assert report.counts.with_sbom == 0
    assert report.counts.without_sbom == 0


def test_check_sbom_probes_only_covered() -> None:
    reg = _reg(referrers={f"{_REPO}:7.1": [_sbom_ref(f"{_REPO}:7.1")]})
    report = audit_coverage(
        registry=reg,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        check_sbom=True,
    )
    by = {o.image_ref: o for o in report.outcomes}
    assert by[f"{_REPO}:7.1"].sbom is True
    assert by[f"{_REPO}:7.2"].sbom is None  # uncovered -> not probed
    assert report.counts.with_sbom == 1
    assert report.counts.without_sbom == 0


def test_check_sbom_covered_without_referrer_is_without_sbom() -> None:
    report = audit_coverage(
        registry=_reg(),
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        check_sbom=True,
    )
    by = {o.image_ref: o for o in report.outcomes}
    assert by[f"{_REPO}:7.1"].sbom is False
    assert report.counts.with_sbom == 0
    assert report.counts.without_sbom == 1


def test_check_sbom_matches_cyclonedx_too() -> None:
    cdx_ref = _sbom_ref(f"{_REPO}:7.1", "application/vnd.cyclonedx+json")
    reg = _reg(referrers={f"{_REPO}:7.1": [cdx_ref]})
    report = audit_coverage(
        registry=reg,
        roster=_ROSTER,
        only_registry=None,
        label_prefix="io.knock",
        check_sbom=True,
    )
    by = {o.image_ref: o for o in report.outcomes}
    assert by[f"{_REPO}:7.1"].sbom is True


def test_limit_caps_the_walk() -> None:
    report = audit_coverage(
        registry=_reg(), roster=_ROSTER, only_registry=None, label_prefix="io.knock", limit=1
    )
    assert len(report.outcomes) == 1
    assert report.counts.scanned == 1


def test_limit_above_total_returns_all() -> None:
    report = audit_coverage(
        registry=_reg(), roster=_ROSTER, only_registry=None, label_prefix="io.knock", limit=99
    )
    assert report.counts.scanned == 2


def test_limit_none_is_unbounded() -> None:
    report = audit_coverage(
        registry=_reg(), roster=_ROSTER, only_registry=None, label_prefix="io.knock", limit=None
    )
    assert report.counts.scanned == 2


def test_limit_short_circuits_before_second_registry() -> None:
    """islice stops the generator inside the first registry, so ensure_registry_session
    is never called for the second host — its session is never established.

    Non-vacuousness: a companion call with limit=None confirms that WITHOUT the cap,
    both hosts DO get a session (both appear in reg.configured).
    """
    _HOST1 = "reg1.example"
    _HOST2 = "reg2.example"
    _REPO1 = f"{_HOST1}/lib/redis"
    _REPO2 = f"{_HOST2}/lib/nginx"

    roster = {
        "reg1": RegistryConfig(host=_HOST1, username="u1", password="p1"),
        "reg2": RegistryConfig(host=_HOST2, username="u2", password="p2"),
    }

    def _make_reg() -> FakeRegistryPort:
        return FakeRegistryPort(
            repositories={
                _HOST1: ["lib/redis"],
                _HOST2: ["lib/nginx"],
            },
            tags={
                _REPO1: ["7.1", "7.2"],  # two images — limit=1 stops here
                _REPO2: ["1.25"],
            },
            annotations={
                f"{_REPO1}:7.1": {"io.knock.policy": "redis"},
                f"{_REPO1}:7.2": {},
                f"{_REPO2}:1.25": {},
            },
        )

    # With limit=1: only the first image from HOST1 is scanned;
    # HOST2's session is never established.
    reg_limited = _make_reg()
    report = audit_coverage(
        registry=reg_limited,
        roster=roster,
        only_registry=None,
        label_prefix="io.knock",
        limit=1,
    )
    assert report.counts.scanned == 1
    configured_hosts_limited = [host for host, _tls, _ca in reg_limited.configured]
    assert _HOST1 in configured_hosts_limited, "first host must have a session"
    assert _HOST2 not in configured_hosts_limited, (
        "second host must NOT be logged in when limit cuts inside the first"
    )

    # Non-vacuousness: without the cap, BOTH hosts get sessions.
    reg_full = _make_reg()
    audit_coverage(
        registry=reg_full,
        roster=roster,
        only_registry=None,
        label_prefix="io.knock",
        limit=None,
    )
    configured_hosts_full = [host for host, _tls, _ca in reg_full.configured]
    assert _HOST1 in configured_hosts_full
    assert _HOST2 in configured_hosts_full, "without a limit, the second host IS logged in"


def test_audit_of_a_path_prefixed_registry_walks_only_its_namespace() -> None:
    reg = FakeRegistryPort(
        repositories={"ghcr.io": ["acme/redis", "other/redis"]},
        tags={"ghcr.io/acme/redis": ["7.2.0"], "ghcr.io/other/redis": ["7.2.0"]},
        annotations={
            "ghcr.io/acme/redis:7.2.0": {"io.knock.policy": "redis"},
            "ghcr.io/other/redis:7.2.0": {"io.knock.policy": "not-ours"},
        },
    )
    report = audit_coverage(
        registry=reg,
        roster={"ghcr": RegistryConfig(host="ghcr.io/acme")},
        only_registry=None,
        label_prefix="io.knock",
    )
    assert [o.image_ref for o in report.outcomes] == ["ghcr.io/acme/redis:7.2.0"]
    assert report.counts.scanned == 1


_FALLBACK_TAG = "sha256-3821e65d0f6c0d2b0a2a3f5c6e7d8a9b0c1d2e3f405162738495a6b7c8d9e0f1"


def test_referrers_fallback_tags_are_not_audited_as_images() -> None:
    # On a registry without the referrers API, knock's own SBOM/signature referrers are
    # stored under a `sha256-<digest>` tag in the subject's repo. Counting them as images
    # would report a SILENTLY WRONG coverage number (every one of them "uncovered").
    reg = FakeRegistryPort(
        repositories={_HOST: ["lib/redis"]},
        tags={_REPO: ["7.1", _FALLBACK_TAG]},
        annotations={f"{_REPO}:7.1": {"io.knock.policy": "redis"}},
    )
    report = audit_coverage(
        registry=reg, roster=_ROSTER, only_registry=None, label_prefix="io.knock"
    )
    assert [o.image_ref for o in report.outcomes] == [f"{_REPO}:7.1"]
    assert report.counts.scanned == 1
    assert report.counts.uncovered == 0
    assert audit_exit_code(report, fail_on_uncovered=True) == 0
