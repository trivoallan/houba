from __future__ import annotations

import json

from knock.ports.reporter import Counts, ErrorInfo
from knock.use_cases.report import (
    Operation,
    PolicyReport,
    RunReport,
    TargetReport,
    VariantReport,
    report_exit_code,
    run_report_json_schema,
)


def _ok_policy() -> PolicyReport:
    op = Operation(
        kind="imported", out_tag="7.2.0", src_tag="7.2.0", digest="sha256:a", applied=True
    )
    variant = VariantReport(name="v7", suffix="", totals=Counts(imported=1), operations=[op])
    target = TargetReport(
        dest_repo="harbor.corp/lib/redis",
        variants=[variant],
        operations=[],
        totals=Counts(imported=1),
    )
    return PolicyReport(
        name="redis",
        source="docker.io/library/redis",
        status="ok",
        error=None,
        totals=Counts(imported=1),
        targets=[target],
    )


def test_run_report_round_trips_to_json() -> None:
    report = RunReport(
        mode="apply", status="ok", totals=Counts(imported=1), policies=[_ok_policy()]
    )
    payload = json.loads(report.model_dump_json())
    assert payload["status"] == "ok"
    assert (
        payload["policies"][0]["targets"][0]["variants"][0]["operations"][0]["kind"] == "imported"
    )
    assert payload["totals"]["imported"] == 1


def test_report_exit_code_zero_when_all_ok() -> None:
    report = RunReport(mode="apply", status="ok", totals=Counts(), policies=[_ok_policy()])
    assert report_exit_code(report) == 0


def test_report_exit_code_is_max_of_failures() -> None:
    p_adapter = PolicyReport(
        name="a",
        source="s",
        status="failed",
        error=ErrorInfo(type="RegctlError", message="boom", exit_code=2),
        totals=Counts(),
        targets=[],
    )
    p_internal = PolicyReport(
        name="b",
        source="s",
        status="failed",
        error=ErrorInfo(type="RuntimeError", message="ugh", exit_code=4),
        totals=Counts(),
        targets=[],
    )
    report = RunReport(
        mode="apply", status="failed", totals=Counts(), policies=[p_adapter, p_internal]
    )
    assert report_exit_code(report) == 4


def test_json_schema_is_stable_and_serializable() -> None:
    schema = run_report_json_schema()
    assert schema["title"] == "RunReport"
    json.dumps(schema)  # must not raise


def test_report_exit_code_single_failure() -> None:
    failed = PolicyReport(
        name="a",
        source="s",
        status="failed",
        error=ErrorInfo(type="RegctlError", message="boom", exit_code=2),
        totals=Counts(),
        targets=[],
    )
    report = RunReport(mode="apply", status="failed", totals=Counts(), policies=[failed])
    assert report_exit_code(report) == 2


def test_counts_failed_defaults_to_zero() -> None:
    assert Counts().failed == 0


def test_counts_failed_accepts_nonzero() -> None:
    assert Counts(failed=2).failed == 2


def test_operation_carries_optional_error() -> None:
    op = Operation(
        kind="imported",
        out_tag="7.2.0",
        applied=False,
        error=ErrorInfo(type="RegctlError", message="boom", exit_code=2),
    )
    assert op.error is not None
    assert op.error.exit_code == 2


def test_report_exit_code_walks_operation_errors() -> None:
    failed_op = Operation(
        kind="imported",
        out_tag="7.3.0",
        applied=False,
        error=ErrorInfo(type="RegctlError", message="boom", exit_code=2),
    )
    variant = VariantReport(
        name="v7", suffix="", status="partial", totals=Counts(failed=1), operations=[failed_op]
    )
    target = TargetReport(
        dest_repo="harbor.corp/lib/redis",
        status="partial",
        variants=[variant],
        operations=[],
        totals=Counts(failed=1),
    )
    policy = PolicyReport(
        name="redis",
        source="s",
        status="partial",
        error=None,
        totals=Counts(failed=1),
        targets=[target],
    )
    report = RunReport(mode="apply", status="partial", totals=Counts(failed=1), policies=[policy])
    assert report_exit_code(report) == 2


def test_report_exit_code_walks_target_level_operation_errors() -> None:
    failed_delete = Operation(
        kind="deleted",
        out_tag="9.9.9",
        applied=False,
        error=ErrorInfo(type="RegctlError", message="delete failed", exit_code=2),
    )
    target = TargetReport(
        dest_repo="harbor.corp/lib/redis",
        status="partial",
        variants=[],
        operations=[failed_delete],
        totals=Counts(failed=1),
    )
    policy = PolicyReport(
        name="redis",
        source="s",
        status="partial",
        error=None,
        totals=Counts(failed=1),
        targets=[target],
    )
    report = RunReport(mode="apply", status="partial", totals=Counts(failed=1), policies=[policy])
    assert report_exit_code(report) == 2


def test_json_report_counts_failed_policies() -> None:
    # The text renderer computed `failed_policies` itself, so the JSON envelope — the
    # documented machine contract — reported `totals.failed == 0` alongside
    # `status == "failed"`. A CI consumer could not count the policies that failed;
    # the human reading the same run could.
    failed = PolicyReport(
        name="a",
        source="s",
        status="failed",
        error=ErrorInfo(type="PolicyValidationError", message="boom", exit_code=1),
        totals=Counts(),
        targets=[],
    )
    ok = PolicyReport(name="b", source="s", status="ok", totals=Counts(), targets=[])
    report = RunReport(mode="apply", status="failed", totals=Counts(), policies=[failed, ok])

    assert report.failed_policies == 1
    assert json.loads(report.model_dump_json())["failed_policies"] == 1


def test_failed_policies_is_derived_not_settable() -> None:
    # Derived from `policies`, so it cannot drift from them — the reason it lives on
    # the model rather than being recomputed by each renderer.
    report = RunReport(mode="apply", status="ok", totals=Counts(), policies=[])
    assert report.failed_policies == 0
    assert "failed_policies" in run_report_json_schema()["properties"]
