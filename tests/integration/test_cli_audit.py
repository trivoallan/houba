from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from knock.cli.main import app

runner = CliRunner()


def _env(monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path) -> None:
    monkeypatch.setenv("PATH", f"{fake_bin_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(
        "KNOCK_REGISTRIES", json.dumps({"harbor": {"host": "harbor.example", "tls_verify": False}})
    )
    monkeypatch.setenv("KNOCK_LOG_FORMAT", "json")


def test_audit_reports_uncovered_exit_0_by_default(
    monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path
) -> None:
    _env(monkeypatch, fake_bin_path)
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "repos")
    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["counts"]["uncovered"] >= 1


def test_audit_fail_on_uncovered_flips_exit_to_1(
    monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path
) -> None:
    _env(monkeypatch, fake_bin_path)
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "repos")
    result = runner.invoke(app, ["audit", "--fail-on-uncovered"])
    assert result.exit_code == 1, result.stdout


def test_audit_all_covered_passes_gate(
    monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path
) -> None:
    _env(monkeypatch, fake_bin_path)
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "coverage-covered")
    result = runner.invoke(app, ["audit", "--fail-on-uncovered"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["counts"]["uncovered"] == 0
    assert data["counts"]["covered"] >= 1


def test_audit_signed_reports_signed_count(
    monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path
) -> None:
    _env(monkeypatch, fake_bin_path)
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "coverage-signed")
    result = runner.invoke(app, ["audit", "--signed"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["counts"]["signed"] >= 1
    assert data["counts"]["unsigned"] == 0


def test_audit_fail_on_unsigned_flips_exit_to_1(
    monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path
) -> None:
    _env(monkeypatch, fake_bin_path)
    # covered but no cosign referrer -> unsigned
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "coverage-covered")
    result = runner.invoke(app, ["audit", "--fail-on-unsigned"])  # implies --signed
    assert result.exit_code == 1, result.stdout
    data = json.loads(result.stdout)
    assert data["counts"]["unsigned"] >= 1


def test_audit_sbom_reports_with_sbom_count(
    monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path
) -> None:
    _env(monkeypatch, fake_bin_path)
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "coverage-sbom")
    result = runner.invoke(app, ["audit", "--sbom"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["counts"]["with_sbom"] >= 1
    assert data["counts"]["without_sbom"] == 0
    # per-outcome digest flows through
    assert all(o["digest"] == "sha256:abc123" for o in data["outcomes"])


def test_audit_limit_caps_scanned(monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path) -> None:
    _env(monkeypatch, fake_bin_path)
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "repos")
    result = runner.invoke(app, ["audit", "--limit", "1"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["counts"]["scanned"] == 1


def test_audit_of_a_path_prefixed_registry_logs_in_with_the_bare_host(
    monkeypatch: pytest.MonkeyPatch, fake_bin_path: Path, tmp_path: Path
) -> None:
    log = tmp_path / "regctl.log"
    monkeypatch.setenv("PATH", f"{fake_bin_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(
        "KNOCK_REGISTRIES",
        json.dumps({"ghcr": {"host": "ghcr.io/acme", "username": "u", "password": "p"}}),
    )
    monkeypatch.setenv("KNOCK_LOG_FORMAT", "json")
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "ghcr-namespaced")
    monkeypatch.setenv("FAKE_REGCTL_LOG", str(log))

    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0, result.stdout

    argv = log.read_text().splitlines()
    assert "registry login --user u --pass-stdin ghcr.io" in argv
    assert "repo ls ghcr.io" in argv
    # the namespace filter is applied to the catalog, not to the catalog request
    assert any("ghcr.io/acme/redis" in line for line in argv)
    assert not any("ghcr.io/other/redis" in line for line in argv)
