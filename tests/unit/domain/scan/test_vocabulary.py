from __future__ import annotations

from knock.domain.scan.vocabulary import scan_annotation_vocabulary


def test_vocabulary_lists_common_envelope_keys() -> None:
    vocab = scan_annotation_vocabulary()
    assert vocab["common"] == ["scan.tool", "scan.format", "scan.timestamp", "scan.subject"]


def test_vocabulary_lists_optional_keys() -> None:
    vocab = scan_annotation_vocabulary()
    assert vocab["optional"] == ["scan.tool.version", "scan.ruleset.hash"]


def test_vocabulary_lists_per_format_fact_keys() -> None:
    vocab = scan_annotation_vocabulary()
    assert vocab["facts"]["sarif"] == [
        "scan.vuln.critical",
        "scan.vuln.high",
        "scan.vuln.medium",
        "scan.vuln.low",
        "scan.vuln.unknown",
        "scan.policy.critical",
        "scan.policy.high",
        "scan.policy.medium",
        "scan.policy.low",
        "scan.policy.unknown",
        "scan.policy.passed",
    ]
