"""Structured reconcile result (stdout machine contract).

Tree: run → policies → targets → variants → operations, with `Counts` aggregated
at each level, and the derivations over it (`node_status`, `merge_counts`,
`report_exit_code`). Deletions attach to TargetReport.operations (the domain returns
to_delete at the import/target level, not per variant). Published as JSON Schema.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, computed_field

from knock.ports.reporter import Counts, ErrorInfo, OperationKind

PolicyStatus = Literal["ok", "partial", "failed"]
# VariantReport/TargetReport; kept separate from PolicyStatus to allow future divergence
NodeStatus = Literal["ok", "partial", "failed"]
RunStatus = Literal["ok", "partial", "failed"]
RunMode = Literal["apply", "dry-run"]


class Operation(BaseModel):
    kind: OperationKind
    out_tag: str
    src_tag: str | None = None
    digest: str | None = None  # source/base digest (provenance), NOT the produced image
    applied: bool  # False => planned only (dry-run) or failed
    error: ErrorInfo | None = None  # set => this operation failed
    transform_steps: list[str] | None = None  # applied step names (rebuild); None on a copy
    out_digest: str | None = None  # produced (post-annotate) digest; None unless applied


class VariantReport(BaseModel):
    name: str
    suffix: str
    status: NodeStatus = "ok"
    totals: Counts
    operations: list[Operation]


class TargetReport(BaseModel):
    dest_repo: str
    status: NodeStatus = "ok"
    variants: list[VariantReport]
    operations: list[Operation]  # target-level ops (deletions)
    totals: Counts


class PolicyReport(BaseModel):
    name: str
    source: str
    status: PolicyStatus
    error: ErrorInfo | None = None
    totals: Counts
    targets: list[TargetReport]


class RunReport(BaseModel):
    mode: RunMode
    status: RunStatus
    totals: Counts
    policies: list[PolicyReport]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed_policies(self) -> int:
        """How many policies failed outright.

        `totals` sums *operations*, so a policy that fails before it plans any
        contributes nothing to it — a run reporting `status="failed"` alongside
        `totals.failed == 0` is the normal shape, not a bug. Without this field the
        JSON envelope, which is the documented machine contract, gave a CI consumer
        no way to count those, while the text recap showed it.

        Computed rather than stored so it cannot drift from `policies`.
        """
        return sum(1 for p in self.policies if p.status == "failed")


def node_status(operations: list[Operation]) -> NodeStatus:
    """Classify a node from the operations under it.

    Lives here, not on a planner: it is pure report-shape arithmetic over
    `Operation`, with no coupling to any source class, and every planner needs the
    identical classification.
    """
    if all(op.error is None for op in operations):
        return "ok"
    return "partial" if any(op.error is None for op in operations) else "failed"


def merge_counts(parts: list[Counts]) -> Counts:
    """Sum `Counts` field-wise. Pure, source-class agnostic — see `node_status`."""
    return Counts(
        imported=sum(c.imported for c in parts),
        updated=sum(c.updated for c in parts),
        deleted=sum(c.deleted for c in parts),
        aliased=sum(c.aliased for c in parts),
        skipped=sum(c.skipped for c in parts),
        marked=sum(c.marked for c in parts),
        attested=sum(c.attested for c in parts),
        sbom=sum(c.sbom for c in parts),
        failed=sum(c.failed for c in parts),
    )


def report_exit_code(report: RunReport) -> int:
    """0 when nothing failed; otherwise the worst (max) failure exit code,
    across both policy-level errors and per-operation errors."""
    codes: list[int] = []
    for p in report.policies:
        if p.error is not None:
            codes.append(p.error.exit_code)
        for tgt in p.targets:
            for v in tgt.variants:
                codes += [op.error.exit_code for op in v.operations if op.error is not None]
            codes += [op.error.exit_code for op in tgt.operations if op.error is not None]
    return max(codes) if codes else 0


def run_report_json_schema() -> dict[str, Any]:
    """JSON Schema for a RunReport — published for CI consumers to validate output.

    `mode="serialization"`, not the default `"validation"`: consumers validate what
    knock *emits*, and the two schemas differ. A computed field like
    `failed_policies` is absent from the validation schema — it is never an input —
    so the default would publish a contract that omits a key every run actually
    carries, and a strict consumer would reject valid output.
    """
    return RunReport.model_json_schema(mode="serialization")
