from __future__ import annotations

import json

import pytest

from knock.domain.scan.formats.sarif import SarifMapper
from knock.errors import ScanReportError


def _sarif(results: list[dict], rules: list[dict] | None = None) -> bytes:
    doc = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {"driver": {"name": "trivy", "version": "0.50.1", "rules": rules or []}},
                "results": results,
            }
        ],
    }
    return json.dumps(doc).encode()


def test_recognizes_sarif_by_schema() -> None:
    assert SarifMapper().recognizes(json.loads(_sarif([])))


def test_recognizes_sarif_by_runs_key() -> None:
    assert SarifMapper().recognizes({"version": "2.1.0", "runs": []})


def test_recognizes_rejects_non_sarif() -> None:
    assert not SarifMapper().recognizes({"bomFormat": "CycloneDX"})


def test_tool_and_version_extracted() -> None:
    s = SarifMapper().summarize(_sarif([]))
    assert s.tool == "trivy"
    assert s.tool_version == "0.50.1"


def test_security_severity_on_rule_buckets_by_cvss() -> None:
    rules = [
        {"id": "CVE-1", "properties": {"security-severity": "9.8"}},  # critical
        {"id": "CVE-2", "properties": {"security-severity": "7.5"}},  # high
        {"id": "CVE-3", "properties": {"security-severity": "5.0"}},  # medium
        {"id": "CVE-4", "properties": {"security-severity": "2.0"}},  # low
    ]
    results = [{"ruleId": f"CVE-{i}"} for i in (1, 2, 3, 4)]
    s = SarifMapper().summarize(_sarif(results, rules))
    assert s.facts["vuln.critical"] == "1"
    assert s.facts["vuln.high"] == "1"
    assert s.facts["vuln.medium"] == "1"
    assert s.facts["vuln.low"] == "1"
    assert s.facts["vuln.unknown"] == "0"


def test_level_fallback_when_no_security_severity() -> None:
    results = [{"level": "error"}, {"level": "warning"}, {"level": "note"}]
    s = SarifMapper().summarize(_sarif(results))
    assert s.facts["vuln.high"] == "1"  # error -> high
    assert s.facts["vuln.medium"] == "1"  # warning -> medium
    assert s.facts["vuln.low"] == "1"  # note -> low


def test_result_without_level_or_severity_is_unknown() -> None:
    s = SarifMapper().summarize(_sarif([{"ruleId": "X"}]))
    assert s.facts["vuln.unknown"] == "1"


def test_no_results_all_zero() -> None:
    s = SarifMapper().summarize(_sarif([]))
    assert all(
        s.facts[f"vuln.{b}"] == "0" for b in ("critical", "high", "medium", "low", "unknown")
    )


def test_kind_pass_counts_as_policy_passed() -> None:
    s = SarifMapper().summarize(_sarif([{"ruleId": "R1", "kind": "pass"}]))
    assert s.facts["policy.passed"] == "1"
    assert s.facts["vuln.high"] == "0"  # not miscounted as a vuln


def test_kind_fail_buckets_by_level() -> None:
    s = SarifMapper().summarize(_sarif([{"ruleId": "R1", "kind": "fail", "level": "error"}]))
    assert s.facts["policy.high"] == "1"  # error -> high, as a policy verdict
    assert s.facts["vuln.high"] == "0"


def test_kind_without_severity_or_level_is_policy_unknown() -> None:
    s = SarifMapper().summarize(_sarif([{"ruleId": "R1", "kind": "open"}]))
    assert s.facts["policy.unknown"] == "1"


def test_kind_wins_over_cvss_score() -> None:
    # A verdict that also carries a CVSS score is bucketed by its severity, NOT as a vuln. The
    # analyzer-agnostic split: keyed on the SARIF `kind`, never the tool name.
    s = SarifMapper().summarize(
        _sarif([{"ruleId": "R1", "kind": "fail", "properties": {"security-severity": "9.8"}}])
    )
    assert s.facts["policy.critical"] == "1"
    assert s.facts["vuln.critical"] == "0"


def test_policy_keys_present_and_zero_when_no_results() -> None:
    s = SarifMapper().summarize(_sarif([]))
    assert s.facts["policy.passed"] == "0"
    assert all(
        s.facts[f"policy.{b}"] == "0" for b in ("critical", "high", "medium", "low", "unknown")
    )


def test_ruleset_hash_read_from_a_result_property_bag() -> None:
    # Where a pass receipt carries it (the shape regis emits on a clean run).
    receipt = {"ruleId": "evaluated", "kind": "pass", "properties": {"ruleset_hash": "sha256:abc"}}
    assert SarifMapper().summarize(_sarif([receipt])).facts["ruleset.hash"] == "sha256:abc"


def test_run_level_ruleset_hash_wins_over_a_result_one() -> None:
    # Run level is the profile's canonical home: it survives a breached run, which has no receipt.
    doc = json.loads(_sarif([{"ruleId": "x", "kind": "pass", "properties": {"ruleset_hash": "r"}}]))
    doc["runs"][0]["properties"] = {"ruleset_hash": "sha256:run"}
    assert SarifMapper().summarize(json.dumps(doc).encode()).facts["ruleset.hash"] == "sha256:run"


def test_ruleset_hash_fact_omitted_when_the_producer_declares_none() -> None:
    # Never fabricated: no fingerprint in, no fact out (ADR 0020's rule).
    assert "ruleset.hash" not in SarifMapper().summarize(_sarif([{"ruleId": "x"}])).facts


def test_malformed_json_raises_scan_report_error() -> None:
    with pytest.raises(ScanReportError, match="JSON"):
        SarifMapper().summarize(b"{not json")


def test_missing_runs_raises_scan_report_error() -> None:
    with pytest.raises(ScanReportError, match="runs"):
        SarifMapper().summarize(b'{"version": "2.1.0"}')
