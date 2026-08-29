"""Packaging refuses unsafe trees and orders entries deterministically."""

from __future__ import annotations

import pytest

from knock.domain.packaging import (
    MAX_ARCHIVE_BYTES,
    ArchiveError,
    SourceFile,
    path_escapes_root,
    plan_archive,
)


def f(path: str, size: int = 10, *, symlink: bool = False, executable: bool = False) -> SourceFile:
    return SourceFile(path=path, size=size, is_symlink=symlink, is_executable=executable)


def test_orders_entries_lexicographically() -> None:
    planned = plan_archive([f("skills/b.md"), f(".claude-plugin/plugin.json"), f("skills/a.md")])
    assert [e.path for e in planned] == [
        ".claude-plugin/plugin.json",
        "skills/a.md",
        "skills/b.md",
    ]


def test_preserves_the_executable_bit() -> None:
    planned = plan_archive([f(".claude-plugin/plugin.json"), f("scripts/run.sh", executable=True)])
    modes = {e.path: e.mode for e in planned}
    assert modes["scripts/run.sh"] == 0o755
    assert modes[".claude-plugin/plugin.json"] == 0o644


def test_preserves_the_planned_size() -> None:
    """`ArchiveEntry.size` is what a writer compares its actual byte count against to
    catch the source tree changing between planning and writing — it must be the size
    recorded here, not re-derived later from the filesystem."""
    planned = plan_archive([f(".claude-plugin/plugin.json", size=16), f("scripts/run.sh", size=18)])
    sizes = {e.path: e.size for e in planned}
    assert sizes == {".claude-plugin/plugin.json": 16, "scripts/run.sh": 18}


def test_refuses_a_symlink() -> None:
    with pytest.raises(ArchiveError, match="symlink"):
        plan_archive([f(".claude-plugin/plugin.json"), f("evil", symlink=True)])


def test_refuses_a_parent_traversal_path() -> None:
    with pytest.raises(ArchiveError, match="escapes"):
        plan_archive([f(".claude-plugin/plugin.json"), f("../../evil")])


def test_refuses_an_absolute_path() -> None:
    with pytest.raises(ArchiveError, match="escapes"):
        plan_archive([f(".claude-plugin/plugin.json"), f("/etc/passwd")])


def test_refuses_a_backslash_path() -> None:
    # posixpath treats "\" as an ordinary filename character, so "..\\evil" is a
    # single opaque segment and does not normalise to a traversal — but a consumer
    # that extracts the archive on Windows treats "\" as a separator and would walk
    # out of the root. Refuse the backslash outright rather than trust the platform.
    with pytest.raises(ArchiveError, match="escapes"):
        plan_archive([f(".claude-plugin/plugin.json"), f("..\\evil")])


def test_refuses_an_empty_path() -> None:
    with pytest.raises(ArchiveError, match="escapes"):
        plan_archive([f(".claude-plugin/plugin.json"), f("")])


def test_refuses_a_path_that_normalises_to_the_root() -> None:
    with pytest.raises(ArchiveError, match="escapes"):
        plan_archive([f(".claude-plugin/plugin.json"), f(".")])


def test_refuses_a_tree_over_the_size_bound() -> None:
    with pytest.raises(ArchiveError, match="exceeds"):
        plan_archive([f(".claude-plugin/plugin.json"), f("big", size=MAX_ARCHIVE_BYTES)])


def test_honours_a_lower_caller_supplied_bound() -> None:
    with pytest.raises(ArchiveError, match="exceeds"):
        plan_archive([f(".claude-plugin/plugin.json", size=100)], max_bytes=50)


def test_refuses_a_tree_with_no_plugin_marker() -> None:
    with pytest.raises(ArchiveError, match="no plugin content"):
        plan_archive([f("README.md")])


def test_accepts_a_bare_skill_md() -> None:
    assert [e.path for e in plan_archive([f("SKILL.md")])] == ["SKILL.md"]


def test_refuses_an_empty_tree() -> None:
    with pytest.raises(ArchiveError, match="no plugin content"):
        plan_archive([])


def test_path_escapes_root_is_the_public_predicate_plan_archive_enforces() -> None:
    """`path_escapes_root` is exported (not `_`-prefixed) specifically so
    `knock.adapters.zip_writer.write_archive` can enforce the same rule at the write
    boundary, without duplicating this logic. A direct test here pins its own contract
    independent of `plan_archive`'s wrapping error message."""
    assert path_escapes_root("../evil") is True
    assert path_escapes_root("/etc/passwd") is True
    assert path_escapes_root("a/../../b") is True
    assert path_escapes_root("..\\evil") is True
    assert path_escapes_root("") is True
    assert path_escapes_root(".") is True
    assert path_escapes_root("skills/a.md") is False
    assert path_escapes_root(".claude-plugin/plugin.json") is False


def test_path_escapes_root_refuses_non_canonical_forms() -> None:
    """`a/../b` and `a/./b` do not escape the root — they resolve to a real path inside
    it — but they are still refused, because the reproducibility argument for the
    archive format depends on every entry already being in its one canonical spelling.
    Silently normalising them would let two differently-spelled arcnames alias the same
    real file."""
    assert path_escapes_root("a/../b") is True
    assert path_escapes_root("a/./b") is True
    assert path_escapes_root("skills/") is True
    assert path_escapes_root("skills/a.md") is False  # already canonical
