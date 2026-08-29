"""Render a RunReport to a stream: `json` (machine contract) or `text` (human recap).

Result goes to stdout; the structlog journal goes to stderr. `--verbose` unfolds
targets/variants/operations under each policy.  The module also provides
`render_scan_outcome` for the `knock attach` scan outcome (`text`/`json`)."""

from __future__ import annotations

import json
from typing import TextIO

from knock.domain.verify import VerifyReport
from knock.use_cases.attach import ScanOutcome
from knock.use_cases.report import Operation, RunReport


def _op_line(op: Operation) -> str:
    src = f"  ⇐ {op.src_tag}" if op.src_tag else ""
    if op.error is not None:
        return (
            f"        {op.kind:<9}{op.out_tag}{src}  FAILED: {op.error.type}: {op.error.message}\n"
        )
    planned = "" if op.applied else "  (planned)"
    return f"        {op.kind:<9}{op.out_tag}{src}{planned}\n"


def render_report(report: RunReport, *, fmt: str, verbose: bool, stream: TextIO) -> None:
    if fmt == "json":
        stream.write(report.model_dump_json() + "\n")
        return

    for p in report.policies:
        if p.error is not None:
            # Policy-level failure: the orchestration itself threw (bad plan, list-tags,
            # collision). A "failed" status with error=None means every operation failed
            # instead — that renders as the totals line below, marked ✗ FAILED.
            stream.write(f"✗ {p.name}  FAILED: {p.error.type}: {p.error.message}\n")
        else:
            t = p.totals
            mark = {"ok": "✓", "partial": "≈", "failed": "✗"}[p.status]
            label = {"ok": "", "partial": "  PARTIAL", "failed": "  FAILED"}[p.status]
            stream.write(
                f"{mark} {p.name}{label}  imported={t.imported} updated={t.updated} "
                f"deleted={t.deleted} aliased={t.aliased} skipped={t.skipped} "
                f"marked={t.marked} attested={t.attested} sbom={t.sbom} failed={t.failed}\n"
            )
        if verbose:
            for tgt in p.targets:
                stream.write(f"    → {tgt.dest_repo}\n")
                for v in tgt.variants:
                    for op in v.operations:
                        stream.write(_op_line(op))
                for op in tgt.operations:
                    stream.write(_op_line(op))

    t = report.totals
    stream.write(
        f"reconcile [{report.mode}] status={report.status}  "
        f"imported={t.imported} updated={t.updated} deleted={t.deleted} "
        f"aliased={t.aliased} skipped={t.skipped} marked={t.marked} "
        f"attested={t.attested} sbom={t.sbom} failed={t.failed} "
        f"failed_policies={report.failed_policies}\n"
    )


def render_verify_report(report: VerifyReport, *, fmt: str, stream: TextIO) -> None:
    if fmt == "json":
        stream.write(
            json.dumps(
                {
                    "passed": report.passed,
                    "requirements": [
                        {"requirement": o.requirement.value, "passed": o.passed, "detail": o.detail}
                        for o in report.outcomes
                    ],
                }
            )
            + "\n"
        )
        return
    for o in report.outcomes:
        mark = "✓" if o.passed else "✗"
        stream.write(f"{mark} {o.requirement.value}: {o.detail}\n")
    stream.write(f"verify {'PASS' if report.passed else 'FAIL'}\n")


def render_scan_outcome(outcome: ScanOutcome, *, fmt: str, stream: TextIO) -> None:
    if fmt == "json":
        stream.write(
            json.dumps(
                {
                    "subjectDigest": outcome.subject_digest,
                    "referrerDigest": outcome.referrer_digest,
                    "tool": outcome.tool,
                    "toolVersion": outcome.tool_version,
                    "format": outcome.format,
                    "facts": outcome.facts,
                    "timestamp": outcome.timestamp.isoformat(),
                    "attestation": (
                        {
                            "predicateType": outcome.attestation.predicate_type,
                            "referrerDigest": outcome.attestation.referrer_digest,
                        }
                        if outcome.attestation is not None
                        else None
                    ),
                }
            )
            + "\n"
        )
        return
    facts = " ".join(f"{k}={v}" for k, v in outcome.facts.items())
    stream.write(
        f"attached {outcome.format} scan ({outcome.tool} {outcome.tool_version}) "
        f"→ {outcome.referrer_digest}\n  subject={outcome.subject_digest}  {facts}\n"
    )
    if outcome.attestation is not None:
        stream.write(
            f"  signed: {outcome.attestation.predicate_type} "
            f"→ {outcome.attestation.referrer_digest}\n"
        )
