"""Describing a real tree for the pure planner — including what must never be described."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from knock.adapters.tree_walker import walk_tree
from knock.domain.packaging import SourceFile
from knock.errors import ArchiveSourceReadError, exit_code_for

RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _paths(root: Path) -> list[str]:
    return [file.path for file in walk_tree(root)]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "skills" / "nested").mkdir(parents=True)
    (root / "SKILL.md").write_text("# probe\n")
    (root / "skills" / "nested" / "a.md").write_text("a\n")
    return root


def test_lists_every_file_relative_to_the_root_in_posix_form(tree: Path) -> None:
    assert _paths(tree) == ["SKILL.md", "skills/nested/a.md"]


def test_records_the_measured_size(tree: Path) -> None:
    assert walk_tree(tree)[0] == SourceFile(path="SKILL.md", size=8)


def test_records_the_executable_bit(tree: Path) -> None:
    (tree / "run.sh").write_text("#!/bin/sh\n")
    (tree / "run.sh").chmod(0o755)
    executable = {file.path: file.is_executable for file in walk_tree(tree)}
    assert executable == {"SKILL.md": False, "run.sh": True, "skills/nested/a.md": False}


def test_reports_a_symlinked_file_without_following_it(tree: Path) -> None:
    (tree / "evil").symlink_to("/etc/passwd")
    reported = [file for file in walk_tree(tree) if file.path == "evil"]
    # `size=0`, not the target's length: reporting it is what makes `plan_archive` refuse
    # the tree, and stat'ing through the link would fail outright on a broken one.
    assert reported == [SourceFile(path="evil", size=0, is_symlink=True)]


def test_reports_a_symlinked_directory_rather_than_silently_dropping_it(tree: Path) -> None:
    # `os.walk(followlinks=False)` yields a symlinked directory in `dirnames` and never in
    # `filenames`, so a walker that only looks at `filenames` omits it from the archive
    # *silently* — no refusal, no entry, a tree packaged as something it is not.
    (tree / "outside").symlink_to("/etc", target_is_directory=True)
    assert SourceFile(path="outside", size=0, is_symlink=True) in walk_tree(tree)


def test_git_metadata_is_excluded(tree: Path) -> None:
    # `GitAdapter.fetch` runs `git init` *in* the workdir and returns that workdir as the
    # root, so the fetched tree always carries `.git/`. `.git/config` holds the remote URL,
    # which in a real deployment can embed a credential.
    (tree / ".git" / "hooks").mkdir(parents=True)
    (tree / ".git" / "config").write_text("[remote]\n\turl = https://t:s3cr3t@example.com/r\n")
    (tree / ".git" / "hooks" / "pre-commit.sample").write_text("#!/bin/sh\n")
    assert _paths(tree) == ["SKILL.md", "skills/nested/a.md"]


def test_a_nested_git_directory_is_excluded_too(tree: Path) -> None:
    # A submodule or a vendored checkout: still repository internals, still never content.
    (tree / "skills" / "vendored" / ".git").mkdir(parents=True)
    (tree / "skills" / "vendored" / ".git" / "config").write_text("[core]\n")
    assert _paths(tree) == ["SKILL.md", "skills/nested/a.md"]


def test_a_gitfile_is_excluded(tree: Path) -> None:
    # A worktree or submodule checkout has `.git` as a *file* holding `gitdir: <abs path>`,
    # which leaks a path on the intake machine and is meaningless to any consumer.
    (tree / ".git").write_text("gitdir: /var/tmp/knock-intake/.git/modules/probe\n")
    assert _paths(tree) == ["SKILL.md", "skills/nested/a.md"]


def test_git_authored_content_is_kept(tree: Path) -> None:
    # `.gitignore` and `.gitattributes` are tracked files an upstream author wrote, not
    # internals: excluding by prefix rather than by exact name would swallow them.
    (tree / ".gitignore").write_text("*.pyc\n")
    (tree / ".gitattributes").write_text("* text=auto\n")
    assert _paths(tree) == [".gitattributes", ".gitignore", "SKILL.md", "skills/nested/a.md"]


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root ignores directory permissions")
def test_an_unlistable_directory_is_refused_not_silently_skipped(tree: Path) -> None:
    # `os.walk` swallows listing errors by default, which would package a *subset* of the
    # tree under a stamp asserting it is the whole upstream revision.
    locked = tree / "skills" / "nested"
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(ArchiveSourceReadError) as exc_info:
            walk_tree(tree)
    finally:
        os.chmod(locked, 0o755)  # so pytest can clean up tmp_path afterward
    assert exit_code_for(exc_info.value) == 2


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root ignores directory permissions")
def test_an_unstattable_file_is_wrapped_not_raised_bare(tree: Path) -> None:
    # A readable-but-not-searchable directory (r--) lists its entries and refuses to stat
    # them. A bare OSError here is not a KnockError, so `cli/main.py` would never see it:
    # the operator gets a traceback and exit 1, which in knock's vocabulary means "your
    # input is invalid" for what is really a filesystem fault.
    locked = tree / "skills" / "nested"
    os.chmod(locked, 0o400)
    try:
        with pytest.raises(ArchiveSourceReadError, match="could not stat") as exc_info:
            walk_tree(tree)
    finally:
        os.chmod(locked, 0o755)  # so pytest can clean up tmp_path afterward
    assert exit_code_for(exc_info.value) == 2
