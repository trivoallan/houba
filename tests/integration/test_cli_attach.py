from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from knock.cli.main import app
from knock.errors import UnknownFormatError

runner = CliRunner()

SARIF = json.dumps(
    {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{"tool": {"driver": {"name": "trivy", "version": "0.50.1"}}, "results": []}],
    }
)

SARIF_WITH_CRITICAL = json.dumps(
    {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "trivy",
                        "version": "0.50.1",
                        "rules": [{"id": "CVE-1", "properties": {"security-severity": "9.8"}}],
                    }
                },
                "results": [{"ruleId": "CVE-1", "level": "error", "message": {"text": "x"}}],
            }
        ],
    }
)

SARIF_MEDIUM_ONLY = json.dumps(
    {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "trivy",
                        "version": "0.50.1",
                        "rules": [{"id": "CVE-2", "properties": {"security-severity": "5.0"}}],
                    }
                },
                "results": [{"ruleId": "CVE-2", "level": "warning", "message": {"text": "x"}}],
            }
        ],
    }
)


def test_attach_runs_and_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv(
        "FAKE_REGCTL_SCENARIO", "default"
    )  # inspect → sha256:abc123, empty annotations
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF)
    result = runner.invoke(app, ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report)])
    assert result.exit_code == 0, result.stdout
    assert "attached sarif scan" in result.stdout
    # The rendered referrer digest must be a real manifest digest, not the bare
    # reference the fake regctl used to echo back for every `artifact put` call.
    digest = "sha256:a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4"
    assert digest in result.stdout


def test_attach_json_output_is_parseable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF)
    result = runner.invoke(
        app,
        ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report), "--output", "json"],
    )
    assert result.exit_code == 0, result.stdout
    last = [ln for ln in result.stdout.splitlines() if ln.strip()][-1]
    payload = json.loads(last)
    assert payload["format"] == "sarif"
    assert payload["subjectDigest"] == "sha256:abc123"
    assert payload["attestation"] is None  # no signer configured → unsigned


def test_attach_unrecognized_report_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    report = tmp_path / "junk.txt"
    report.write_text("not a scan report")
    result = runner.invoke(app, ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report)])
    assert result.exit_code != 0
    assert isinstance(result.exception, UnknownFormatError)


def test_attach_reads_report_from_stdin(
    monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    result = runner.invoke(
        app, ["attach", "harbor.corp/lib/redis:7.2.0", "--report", "-"], input=SARIF
    )
    assert result.exit_code == 0, result.stdout
    assert "attached sarif scan" in result.stdout


def test_attach_format_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF)
    result = runner.invoke(
        app,
        ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report), "--format", "sarif"],
    )
    assert result.exit_code == 0, result.stdout
    assert "attached sarif scan" in result.stdout


def test_attach_signs_when_signer_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    monkeypatch.setenv("FAKE_COSIGN_SCENARIO", "success")
    monkeypatch.setenv("KNOCK_ATTEST_SIGNER", "keyless")
    monkeypatch.setenv("KNOCK_ATTEST_BUILDER_ID", "knock://ci")
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF)
    result = runner.invoke(app, ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report)])
    assert result.exit_code == 0, result.stdout
    assert "signed:" in result.stdout
    assert "https://knock.dev/predicate/scan/v1" in result.stdout


def test_attach_unsigned_when_no_signer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    monkeypatch.delenv("KNOCK_ATTEST_SIGNER", raising=False)
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF)
    result = runner.invoke(app, ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report)])
    assert result.exit_code == 0, result.stdout
    assert "signed:" not in result.stdout


def test_attach_fail_on_gates_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF_WITH_CRITICAL)
    result = runner.invoke(
        app,
        ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report), "--fail-on", "critical"],
    )
    assert result.exit_code == 1
    assert "at or above critical" in result.stderr  # the gate message explains the non-zero exit


def test_attach_fail_on_below_threshold_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF_MEDIUM_ONLY)
    result = runner.invoke(
        app,
        ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report), "--fail-on", "critical"],
    )
    assert result.exit_code == 0


def test_attach_no_fail_on_is_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF_WITH_CRITICAL)
    result = runner.invoke(app, ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report)])
    assert result.exit_code == 0


def test_attach_authenticates_via_roster(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin_path: Path
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    log = tmp_path / "regctl.log"
    monkeypatch.setenv("FAKE_REGCTL_LOG", str(log))
    monkeypatch.setenv(
        "KNOCK_REGISTRIES",
        '{"prod": {"host": "harbor.corp", "username": "u", "password": "p"}}',
    )
    report = tmp_path / "scan.sarif.json"
    report.write_text(SARIF)
    result = runner.invoke(app, ["attach", "harbor.corp/lib/redis:7.2.0", "--report", str(report)])
    assert result.exit_code == 0, result.stdout
    assert "registry login" in log.read_text()
