"""Exercises the real git binary against a repository built by the fixture."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from knock.adapters import git_cli
from knock.adapters.git_cli import GitAdapter
from knock.errors import InternalError, SourceError, SourcePathError

# Do not "simplify" this env away — without it the suite fails on a developer machine,
# and not because of the code under test:
#   * GIT_CONFIG_GLOBAL / GIT_CONFIG_SYSTEM: a developer with `commit.gpgsign = true` or
#     `tag.gpgsign = true` in ~/.gitconfig gets exit 128 from the fixture's own `git
#     commit` / `git tag`, before the adapter is ever called. Signing is common enough
#     that the fixture has to build the same repository on every machine.
#   * GIT_TERMINAL_PROMPT=0: a fetch that decides to ask for credentials would otherwise
#     block on a prompt nobody can answer and hang the suite.
_ISOLATED_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, env=_ISOLATED_ENV)


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@example.com"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    (repo / "SKILL.md").write_text("# probe\n")
    _run(["git", "add", "SKILL.md"], repo)
    _run(["git", "commit", "-q", "-m", "first"], repo)
    _run(["git", "tag", "v1.0.0"], repo)
    return repo


def test_fetches_a_tag_and_resolves_it_to_a_commit_sha(upstream: Path, tmp_path: Path) -> None:
    fetched = GitAdapter().fetch(str(upstream), "v1.0.0", tmp_path / "work")
    assert (fetched.root / "SKILL.md").read_text() == "# probe\n"
    assert len(fetched.revision) == 40
    assert fetched.revision.isalnum()


def test_the_same_ref_always_resolves_to_the_same_revision(upstream: Path, tmp_path: Path) -> None:
    first = GitAdapter().fetch(str(upstream), "v1.0.0", tmp_path / "a")
    second = GitAdapter().fetch(str(upstream), "v1.0.0", tmp_path / "b")
    assert first.revision == second.revision


def test_a_subdirectory_becomes_the_root(upstream: Path, tmp_path: Path) -> None:
    nested = upstream / "packages" / "inner"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("# inner\n")
    _run(["git", "add", "-A"], upstream)
    _run(["git", "commit", "-q", "-m", "nested"], upstream)
    fetched = GitAdapter().fetch(str(upstream), "main", tmp_path / "work", path="packages/inner")
    assert (fetched.root / "SKILL.md").read_text() == "# inner\n"


def test_an_unknown_ref_raises(upstream: Path, tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        GitAdapter().fetch(str(upstream), "v9.9.9", tmp_path / "work")


def test_a_missing_subdirectory_raises(upstream: Path, tmp_path: Path) -> None:
    with pytest.raises(SourcePathError, match="path not found"):
        GitAdapter().fetch(str(upstream), "main", tmp_path / "work", path="nope")


@pytest.mark.parametrize("path", ["../escape", "packages/../../escape", "/etc"])
def test_a_traversing_path_is_refused(upstream: Path, tmp_path: Path, path: str) -> None:
    (tmp_path / "escape").mkdir()
    with pytest.raises(SourcePathError, match="escapes the fetched tree"):
        GitAdapter().fetch(str(upstream), "main", tmp_path / "work", path=path)


def test_a_symlinked_path_that_leaves_the_tree_is_refused(upstream: Path, tmp_path: Path) -> None:
    # A hostile repository can ship a symlink; `is_dir()` alone would confirm it exists.
    outside = tmp_path / "outside"
    outside.mkdir()
    (upstream / "packages").symlink_to(outside, target_is_directory=True)
    _run(["git", "add", "-A"], upstream)
    _run(["git", "commit", "-q", "-m", "symlink"], upstream)
    with pytest.raises(SourcePathError, match="escapes the fetched tree"):
        GitAdapter().fetch(str(upstream), "main", tmp_path / "work", path="packages")


def test_a_non_empty_workdir_is_refused(upstream: Path, tmp_path: Path) -> None:
    # Refusing, not cleaning: git init over a previous fetch's leftovers would fold them
    # into this run's archive. InternalError (exit 4) — the caller picks the workdir.
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "leftover.txt").write_text("from a previous fetch\n")
    with pytest.raises(InternalError, match="not empty"):
        GitAdapter().fetch(str(upstream), "main", workdir)


def test_a_ref_beginning_with_a_dash_cannot_smuggle_a_git_option(
    upstream: Path, tmp_path: Path
) -> None:
    # Without `--`, git parses this as `--upload-pack=<cmd>` and executes <cmd>.
    marker = tmp_path / "pwned"
    payload = tmp_path / "payload.sh"
    payload.write_text(f"#!/bin/sh\ntouch {marker}\n")
    payload.chmod(0o755)
    with pytest.raises(SourceError):
        GitAdapter().fetch(str(upstream), f"--upload-pack={payload}", tmp_path / "work")
    assert not marker.exists()


def test_a_nul_byte_in_a_ref_is_reported_not_raised_as_a_traceback(
    upstream: Path, tmp_path: Path
) -> None:
    # subprocess raises ValueError, not OSError, and neither is a KnockError.
    with pytest.raises(SourceError):
        GitAdapter().fetch(str(upstream), "main\x00", tmp_path / "work")


def test_a_missing_git_binary_is_reported_as_a_source_error(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="could not be executed"):
        GitAdapter(binary=str(tmp_path / "no-such-git")).fetch(
            "https://example.invalid/r.git", "main", tmp_path / "work"
        )


def test_a_git_that_is_not_on_path_is_reported_as_a_source_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SourceError, match="not found in PATH"):
        GitAdapter().fetch("https://example.invalid/r.git", "main", tmp_path / "work")


def test_a_wedged_git_is_killed_rather_than_hanging_the_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hostile or wedged server must not be able to pin an intake worker forever.
    monkeypatch.setattr(git_cli, "_TIMEOUT_SECONDS", 1)
    wedged = tmp_path / "wedged-git"
    wedged.write_text("#!/bin/sh\nsleep 30\n")
    wedged.chmod(0o755)
    with pytest.raises(SourceError, match="timed out"):
        GitAdapter(binary=str(wedged)).fetch(
            "https://example.invalid/r.git", "main", tmp_path / "work"
        )
