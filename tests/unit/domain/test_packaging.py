"""Packaging refuses unsafe trees and orders entries deterministically."""

from __future__ import annotations

import pytest

from knock.domain.packaging import (
    MAX_ARCHIVE_BYTES,
    SourceFile,
    plan_archive,
)
from knock.errors import ArchiveError, ArchiveLayoutError


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


def test_refuses_a_symlink() -> None:
    with pytest.raises(ArchiveError, match="symlink"):
        plan_archive([f(".claude-plugin/plugin.json"), f("evil", symlink=True)])


def test_reports_every_symlink_together() -> None:
    with pytest.raises(ArchiveError) as excinfo:
        plan_archive([f(".claude-plugin/plugin.json"), f("a", symlink=True), f("b", symlink=True)])
    assert "a" in str(excinfo.value)
    assert "b" in str(excinfo.value)


def test_refuses_a_parent_traversal_path() -> None:
    with pytest.raises(ArchiveError, match="escape"):
        plan_archive([f(".claude-plugin/plugin.json"), f("../../evil")])


def test_refuses_a_bare_double_dot_path() -> None:
    with pytest.raises(ArchiveError, match="escape"):
        plan_archive([f(".claude-plugin/plugin.json"), f("..")])


def test_reports_every_escaping_path_together() -> None:
    with pytest.raises(ArchiveError) as excinfo:
        plan_archive([f(".claude-plugin/plugin.json"), f("../a"), f("../b")])
    assert "../a" in str(excinfo.value)
    assert "../b" in str(excinfo.value)


def test_refuses_an_absolute_path() -> None:
    with pytest.raises(ArchiveError, match="escape"):
        plan_archive([f(".claude-plugin/plugin.json"), f("/etc/passwd")])


def test_refuses_an_empty_path() -> None:
    with pytest.raises(ArchiveError, match="escape"):
        plan_archive([f(".claude-plugin/plugin.json"), f("")])


def test_refuses_a_path_that_normalises_to_the_root() -> None:
    with pytest.raises(ArchiveError, match="escape"):
        plan_archive([f(".claude-plugin/plugin.json"), f(".")])


def test_refuses_a_backslash_path() -> None:
    # posixpath treats "\" as an ordinary filename character, so "..\\evil" is a
    # single opaque segment and does not normalise to a traversal under POSIX
    # semantics — it is refused for a different, explicitly-named reason: a consumer
    # that extracts the archive on a system where "\" is a path separator would read
    # it as "..\evil" and walk out of the root there. The message must not claim it
    # "escapes the root", since under POSIX it plainly does not.
    with pytest.raises(ArchiveError, match="backslash") as excinfo:
        plan_archive([f(".claude-plugin/plugin.json"), f("..\\evil")])
    assert "escapes" not in str(excinfo.value)


def test_normalises_a_dot_slash_prefixed_file_marker() -> None:
    assert [e.path for e in plan_archive([f("./SKILL.md")])] == ["SKILL.md"]


def test_normalises_a_dot_slash_prefixed_directory_member() -> None:
    planned = plan_archive([f("./skills/a.md")])
    assert [e.path for e in planned] == ["skills/a.md"]


def test_stores_the_canonical_path_for_a_redundant_traversal() -> None:
    # "skills/../skills/a.md" resolves inside the root, so it is not an escape — but a
    # ".." inside a member name is exactly the shape zip-slip detectors flag, and it is
    # not the path a human review would recognise. The planned entry must be canonical.
    planned = plan_archive([f("skills/../skills/a.md")])
    assert [e.path for e in planned] == ["skills/a.md"]


def test_refuses_a_duplicate_path() -> None:
    with pytest.raises(ArchiveError, match="collide"):
        plan_archive([f("SKILL.md"), f("SKILL.md")])


def test_refuses_a_case_insensitive_collision() -> None:
    with pytest.raises(ArchiveError, match="collide"):
        plan_archive([f("SKILL.md"), f("skill.MD")])


def test_refuses_a_duplicate_created_by_normalisation() -> None:
    with pytest.raises(ArchiveError, match="collide"):
        plan_archive([f("./SKILL.md"), f("SKILL.md")])


def test_refuses_a_tree_over_the_size_bound() -> None:
    with pytest.raises(ArchiveError, match="exceeds"):
        plan_archive([f(".claude-plugin/plugin.json"), f("big", size=MAX_ARCHIVE_BYTES)])


def test_accepts_a_tree_exactly_at_the_size_bound() -> None:
    planned = plan_archive([f(".claude-plugin/plugin.json", size=50)], max_bytes=50)
    assert [e.path for e in planned] == [".claude-plugin/plugin.json"]


def test_honours_a_lower_caller_supplied_bound() -> None:
    with pytest.raises(ArchiveError, match="exceeds"):
        plan_archive([f(".claude-plugin/plugin.json", size=100)], max_bytes=50)


def test_refuses_a_negative_declared_size() -> None:
    with pytest.raises(ArchiveError, match="negative"):
        plan_archive([f("SKILL.md", size=-1)])


def test_refuses_a_tree_with_no_plugin_marker() -> None:
    with pytest.raises(ArchiveLayoutError, match="no plugin content"):
        plan_archive([f("README.md")])


def test_refuses_a_bare_file_named_after_a_directory_marker() -> None:
    # "skills" with no "/" after it is a file, not the skills/ directory, and must not
    # satisfy the directory marker just because the two strings match.
    with pytest.raises(ArchiveLayoutError, match="no plugin content"):
        plan_archive([f("skills")])


def test_marker_matching_is_case_sensitive() -> None:
    with pytest.raises(ArchiveLayoutError, match="no plugin content"):
        plan_archive([f("Skills/a.md")])


def test_accepts_a_bare_skill_md() -> None:
    assert [e.path for e in plan_archive([f("SKILL.md")])] == ["SKILL.md"]


def test_refuses_an_empty_tree() -> None:
    with pytest.raises(ArchiveLayoutError, match="no plugin content"):
        plan_archive([])
