"""put_artifact builds the regctl invocation that pushes a standalone artifact."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from knock.adapters.regctl_cli import RegctlAdapter


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: Any) -> Any:
        calls.append(args)

        class Done:
            stdout = "sha256:" + "a" * 64 + "\n"
            stderr = ""
            returncode = 0

        return Done()

    monkeypatch.setattr("knock.adapters.regctl_cli.subprocess.run", fake_run)
    return calls


def test_builds_the_artifact_put_invocation(captured: list[list[str]], tmp_path: Path) -> None:
    blob = tmp_path / "skill.zip"
    blob.write_bytes(b"PK\x03\x04")
    # RegctlAdapter.__init__ requires an explicit `binary=` to exist on disk (it's a real
    # is_file() check, independent of the subprocess.run patch below), so this stands in
    # for the regctl binary. Its contents are irrelevant since subprocess.run is mocked.
    fake_regctl = tmp_path / "regctl"
    fake_regctl.write_bytes(b"")
    adapter = RegctlAdapter(binary=str(fake_regctl))
    digest = adapter.put_artifact(
        "registry.example/skills/probe:1.0.0",
        artifact_type="application/vnd.knock.skill.v1",
        blob_path=blob,
        media_type="application/zip",
        annotations={"org.opencontainers.image.revision": "deadbeef"},
    )
    argv = captured[0]
    assert argv[:3] == [str(fake_regctl), "artifact", "put"]
    assert "--artifact-type" in argv
    assert argv[argv.index("--artifact-type") + 1] == "application/vnd.knock.skill.v1"
    assert argv[argv.index("--file-media-type") + 1] == "application/zip"
    assert argv[argv.index("--file") + 1] == str(blob)
    assert argv[argv.index("--annotation") + 1] == "org.opencontainers.image.revision=deadbeef"
    assert argv[-1] == "registry.example/skills/probe:1.0.0"
    assert digest == "sha256:" + "a" * 64


def test_annotations_are_sorted_so_the_invocation_is_stable(
    captured: list[list[str]], tmp_path: Path
) -> None:
    blob = tmp_path / "skill.zip"
    blob.write_bytes(b"PK\x03\x04")
    fake_regctl = tmp_path / "regctl"
    fake_regctl.write_bytes(b"")
    RegctlAdapter(binary=str(fake_regctl)).put_artifact(
        "registry.example/skills/probe:1.0.0",
        artifact_type="application/vnd.knock.skill.v1",
        blob_path=blob,
        media_type="application/zip",
        annotations={"b.key": "2", "a.key": "1"},
    )
    argv = captured[0]
    first = argv.index("--annotation")
    assert argv[first + 1] == "a.key=1"
    assert argv[first + 3] == "b.key=2"
