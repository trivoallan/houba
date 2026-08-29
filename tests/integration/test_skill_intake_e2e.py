"""End-to-end: a git repo becomes a stamped OCI artifact whose blob digest is its zip's sha256.

This is the only test in the slice where a *real* `git fetch` leaves a real `.git/`
directory in the tree that then gets packaged, and the only one that drives a real
`regctl` far enough to read the manifest back. Every other intake test runs against a
fake source handing back a pristine directory, so the VCS exclusion and the identity of
the layer digest are only ever exercised for real here.

It is hermetic and it **always runs**. The default target is `ocidir://` — a real OCI
layout on disk, written and read by the real regctl binary, with no server, no docker and
no network — because a test that is skipped by default protects nothing. Setting
`KNOCK_TEST_REGISTRY=host:port` adds a second, otherwise identical case against a
genuinely networked registry:

    KNOCK_TEST_REGISTRY=localhost:15000 .venv/bin/pytest tests/integration/test_skill_intake_e2e.py

That case skips when the variable is unset. It is an *extra*, never the only path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from knock.adapters.git_cli import GitAdapter
from knock.adapters.local_archiver import LocalArchiver
from knock.adapters.regctl_cli import RegctlAdapter
from knock.use_cases.intake import IntakeRequest, IntakeResult, intake_skill

REGISTRY = os.environ.get("KNOCK_TEST_REGISTRY")

# Resolved exactly as `RegctlAdapter._resolve` resolves it, so the skip guard and the code
# under test can never disagree about which binary is in play.
REGCTL = shutil.which("regctl")

pytestmark = pytest.mark.skipif(REGCTL is None, reason="regctl not on PATH")

CREATED = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
ARTIFACT_TYPE = "application/vnd.knock.skill.v1"
MEDIA_TYPE = "application/zip"
PREFIX = "io.knock"

# Same isolation as `tests/unit/adapters/test_git_cli.py`, for the same reasons; the
# comment there is the long version. Without it a developer with `commit.gpgsign = true`
# or `tag.gpgsign = true` in ~/.gitconfig gets exit 128 out of this fixture's own `git
# commit`/`git tag`, before any knock code has run.
_ISOLATED_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, env=_ISOLATED_ENV)


def _regctl(*args: str) -> str:
    assert REGCTL is not None  # guaranteed by pytestmark
    return subprocess.run([REGCTL, *args], check=True, capture_output=True, text=True).stdout


def _manifest(ref: str) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(_regctl("manifest", "get", ref, "--format", "{{json .}}"))
    return parsed


def _pull_blob(ref: str, digest: str, destination: Path) -> Path:
    assert REGCTL is not None
    with destination.open("wb") as out:
        subprocess.run([REGCTL, "blob", "get", ref, digest], check=True, stdout=out)
    return destination


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """A real git repository, tagged — not a directory pretending to be one."""
    repo = tmp_path / "upstream"
    (repo / ".claude-plugin").mkdir(parents=True)
    (repo / ".claude-plugin" / "plugin.json").write_text('{"name":"e2e-probe","version":"0.1.0"}')
    (repo / "skills").mkdir()
    (repo / "skills" / "hello.md").write_text("# hello\n")
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@example.com"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-q", "-m", "first"], repo)
    _run(["git", "tag", "v0.1.0"], repo)
    return repo


@dataclass(frozen=True)
class Placed:
    """One completed intake, plus everything read back out of the registry afterwards."""

    ref: str
    result: IntakeResult
    manifest: dict[str, Any]
    workdir: Path
    archive: Path


def _intake(upstream: Path, destination_ref: str, workdir: Path) -> IntakeResult:
    return intake_skill(
        IntakeRequest(
            origin=str(upstream),
            ref="v0.1.0",
            path=None,
            destination_ref=destination_ref,
            title="e2e-probe",
            policy="e2e",
            import_name="release",
            owners=["group:default/platform"],
            vendor="Example Platform",
            workdir=workdir,
        ),
        source=GitAdapter(),
        registry=RegctlAdapter(),
        archiver=LocalArchiver(),
        prefix=PREFIX,
        now=CREATED,
    )


def _place(upstream: Path, destination_ref: str, into: Path) -> Placed:
    """Run one real intake and read the result back out of the registry."""
    workdir = into / "work"
    result = _intake(upstream, destination_ref, workdir)
    manifest = _manifest(destination_ref)
    archive = _pull_blob(destination_ref, manifest["layers"][0]["digest"], into / "pulled.zip")
    return Placed(
        ref=destination_ref, result=result, manifest=manifest, workdir=workdir, archive=archive
    )


@pytest.fixture
def placed(upstream: Path, tmp_path: Path) -> Placed:
    """The hermetic default: a real OCI layout on disk. No server, no network, no skip."""
    return _place(upstream, f"ocidir://{tmp_path / 'layout'}:0.1.0", tmp_path)


def test_the_layer_digest_is_the_sha256_of_the_zip(placed: Placed) -> None:
    """The load-bearing assertion of the whole slice.

    The marketplace manifest pins this digest and the client verifies it. If the OCI layer
    digest and the sha256 knock reports for the archive can drift apart, every downstream
    check is verifying something other than what was published.
    """
    assert placed.manifest["artifactType"] == ARTIFACT_TYPE
    assert placed.manifest["layers"][0]["mediaType"] == MEDIA_TYPE
    assert placed.manifest["layers"][0]["digest"] == f"sha256:{placed.result.blob_sha256}"


def test_the_pulled_blob_is_byte_identical_to_what_was_pushed(placed: Placed) -> None:
    assert hashlib.sha256(placed.archive.read_bytes()).hexdigest() == placed.result.blob_sha256
    assert zipfile.is_zipfile(placed.archive)


def test_the_returned_manifest_digest_is_the_one_the_registry_holds(placed: Placed) -> None:
    """`IntakeResult.manifest_digest` is scraped from regctl's stdout by the adapter; this
    is the only place that confirms it names the manifest the registry actually stored."""
    assert placed.result.manifest_digest == _regctl("manifest", "head", placed.ref).strip()


def test_the_git_directory_never_reaches_the_archive(placed: Placed) -> None:
    """The VCS exclusion, end to end and for real.

    `GitAdapter.fetch` runs `git init` in the workdir and returns that same workdir as the
    tree root, so `.git/` is genuinely there to be packaged — the two assertions on the
    fetched tree below are what keep the exclusion assertion from passing vacuously if
    that ever stops being true. `.git/config` holds the remote URL, which in a real
    deployment can carry a credential, and the artifact is installed on workstations.
    """
    config = placed.workdir / ".git" / "config"
    assert config.is_file(), "the fetched tree has no .git/ — this test would prove nothing"
    assert placed.result.revision in _regctl(
        "manifest", "get", placed.ref, "--format", "{{json .}}"
    )
    members = zipfile.ZipFile(placed.archive).namelist()
    assert [m for m in members if m == ".git" or m.startswith(".git/")] == []
    assert set(members) == {".claude-plugin/plugin.json", "skills/hello.md"}


def test_the_stamp_reads_back_off_the_manifest(placed: Placed, upstream: Path) -> None:
    annotations = placed.manifest["annotations"]
    assert annotations[f"{PREFIX}.policy"] == "e2e"
    assert annotations[f"{PREFIX}.artifact.type"] == "skill"
    assert annotations[f"{PREFIX}.import"] == "release"
    assert annotations[f"{PREFIX}.variant"] == "default"
    assert annotations[f"{PREFIX}.owners"] == "group:default/platform"
    assert annotations["org.opencontainers.image.title"] == "e2e-probe"
    assert annotations["org.opencontainers.image.source"] == str(upstream)
    assert annotations["org.opencontainers.image.revision"] == placed.result.revision


def test_a_source_built_artifact_claims_no_base_image(placed: Placed) -> None:
    """ADR 0020: there is no base image here, so none is fabricated. Task 6 covers the
    image path, which is the only path entitled to these keys."""
    base = [
        k for k in placed.manifest["annotations"] if k.startswith("org.opencontainers.image.base.")
    ]
    assert base == []


def test_two_intakes_of_the_same_commit_place_the_same_artifact(
    upstream: Path, tmp_path: Path
) -> None:
    """Reproducibility is the property `zip_writer` was built for; nothing else proves it
    past the zip itself. Same commit and same `now` in, byte-identical artifact out —
    which is what makes a pinned digest re-derivable rather than a one-off observation.
    Same-process only: cross-machine identity also depends on the zlib build, as
    `adapters/zip_writer.py` says.
    """
    first = _intake(upstream, f"ocidir://{tmp_path / 'a'}:0.1.0", tmp_path / "work-a")
    second = _intake(upstream, f"ocidir://{tmp_path / 'b'}:0.1.0", tmp_path / "work-b")
    assert first.revision == second.revision
    assert first.blob_sha256 == second.blob_sha256
    assert first.manifest_digest == second.manifest_digest


@pytest.mark.skipif(REGISTRY is None, reason="KNOCK_TEST_REGISTRY unset")
def test_every_property_also_holds_against_a_networked_registry(
    upstream: Path, tmp_path: Path
) -> None:
    """The opt-in extra: the same round trip against a genuinely networked registry.

        docker run -d --name knock-e2e -p 15000:5000 registry:2
        regctl registry set --tls disabled localhost:15000
        KNOCK_TEST_REGISTRY=localhost:15000 .venv/bin/pytest \\
            tests/integration/test_skill_intake_e2e.py

    The checks above are reused verbatim rather than restated, so this case can never
    drift into asserting less than the hermetic one — which is the only reason it is safe
    for this single test to be the one thing here that skips.
    """
    assert REGISTRY is not None
    placed = _place(upstream, f"{REGISTRY}/skills/e2e-probe:0.1.0", tmp_path)
    test_the_layer_digest_is_the_sha256_of_the_zip(placed)
    test_the_pulled_blob_is_byte_identical_to_what_was_pushed(placed)
    test_the_returned_manifest_digest_is_the_one_the_registry_holds(placed)
    test_the_git_directory_never_reaches_the_archive(placed)
    test_the_stamp_reads_back_off_the_manifest(placed, upstream)
    test_a_source_built_artifact_claims_no_base_image(placed)
