"""Packaging refuses unsafe trees and orders entries deterministically."""

from __future__ import annotations

import pytest

from knock.domain.packaging import (
    MAX_ARCHIVE_BYTES,
    PLUGIN_MARKER_DIRS,
    PLUGIN_MARKER_FILES,
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


def test_sorts_by_the_normalised_path_not_the_raw_source_path() -> None:
    # Raw source strings would sort "./b.md" ahead of "a.md" (ASCII "." < "a"); the
    # normalised member names must sort as "SKILL.md", "a.md", "b.md".
    planned = plan_archive([f("./b.md"), f("a.md"), f("SKILL.md")])
    assert [e.path for e in planned] == ["SKILL.md", "a.md", "b.md"]


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
    # endswith, not "x in message" — the boilerplate itself contains the letter "b"
    # ("before packaging"), which let a prior version of this test pass vacuously.
    assert str(excinfo.value).endswith(", ".join(sorted(["a", "b"])))


def test_refuses_a_parent_traversal_path() -> None:
    with pytest.raises(ArchiveError, match="escape"):
        plan_archive([f(".claude-plugin/plugin.json"), f("../../evil")])


def test_refuses_a_bare_double_dot_path() -> None:
    with pytest.raises(ArchiveError, match="escape"):
        plan_archive([f(".claude-plugin/plugin.json"), f("..")])


def test_reports_every_escaping_path_together() -> None:
    with pytest.raises(ArchiveError) as excinfo:
        plan_archive([f(".claude-plugin/plugin.json"), f("../a"), f("../b")])
    assert str(excinfo.value).endswith(", ".join(sorted(["../a", "../b"])))


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


def test_reports_every_backslash_path_together() -> None:
    with pytest.raises(ArchiveError) as excinfo:
        plan_archive([f(".claude-plugin/plugin.json"), f("..\\evil1"), f("..\\evil2")])
    assert str(excinfo.value).endswith(", ".join(sorted(["..\\evil1", "..\\evil2"])))


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


def test_normalisation_prevents_a_marker_illusion() -> None:
    # ".claude-plugin/../evil.md" resolves to the single root-level file "evil.md" — it
    # must not be accepted just because its *raw* root segment reads ".claude-plugin".
    with pytest.raises(ArchiveLayoutError, match="no plugin content"):
        plan_archive([f(".claude-plugin/../evil.md")])


def test_refuses_a_duplicate_path() -> None:
    with pytest.raises(ArchiveError) as excinfo:
        plan_archive([f("SKILL.md"), f("SKILL.md")])
    assert str(excinfo.value) == (
        "refusing to package paths that collide as archive members: SKILL.md (listed 2 times)"
    )


def test_refuses_a_case_insensitive_collision() -> None:
    with pytest.raises(ArchiveError) as excinfo:
        plan_archive([f("SKILL.md"), f("skill.MD")])
    assert str(excinfo.value) == (
        "refusing to package paths that collide as archive members: "
        "SKILL.md / skill.MD (collide once packaged)"
    )


def test_refuses_a_duplicate_created_by_normalisation() -> None:
    with pytest.raises(ArchiveError) as excinfo:
        plan_archive([f("./SKILL.md"), f("SKILL.md")])
    assert str(excinfo.value) == (
        "refusing to package paths that collide as archive members: SKILL.md (listed 2 times)"
    )


def test_accepts_unicode_lookalikes_no_target_filesystem_conflates() -> None:
    # str.casefold() would fold "straße" -> "strasse" (full Unicode folding) and refuse
    # this pair, even though neither NTFS's upcase table nor APFS's folding merges them.
    # str.lower() must leave "ß" and "ss" distinct, so this tree is accepted.
    planned = plan_archive(
        [f(".claude-plugin/plugin.json"), f("skills/straße.md"), f("skills/strasse.md")]
    )
    assert {e.path for e in planned} == {
        ".claude-plugin/plugin.json",
        "skills/straße.md",
        "skills/strasse.md",
    }


def test_max_archive_bytes_is_100_mebibytes() -> None:
    # Pins the literal value: every other size test either overrides max_bytes
    # explicitly or reads MAX_ARCHIVE_BYTES symbolically, so none of them would notice
    # this constant drifting away from the SkillSpector INGEST_MAX_BYTES bound.
    assert MAX_ARCHIVE_BYTES == 100 * 1024 * 1024


def test_refuses_a_tree_over_the_size_bound() -> None:
    with pytest.raises(ArchiveError, match="exceeds"):
        plan_archive([f(".claude-plugin/plugin.json"), f("big", size=MAX_ARCHIVE_BYTES)])


def test_size_message_reports_bytes_and_mib_without_swapping_total_and_bound() -> None:
    with pytest.raises(ArchiveError) as excinfo:
        plan_archive([f(".claude-plugin/plugin.json", size=100)], max_bytes=50)
    assert str(excinfo.value) == (
        "tree exceeds the archive bound: 0.00 MiB (100 bytes) > 0.00 MiB (50 bytes) — "
        "trim or exclude files before packaging"
    )


def test_accepts_a_tree_exactly_at_the_size_bound() -> None:
    planned = plan_archive([f(".claude-plugin/plugin.json", size=50)], max_bytes=50)
    assert [e.path for e in planned] == [".claude-plugin/plugin.json"]


def test_honours_a_lower_caller_supplied_bound() -> None:
    with pytest.raises(ArchiveError, match="exceeds"):
        plan_archive([f(".claude-plugin/plugin.json", size=100)], max_bytes=50)


def test_refuses_a_negative_declared_size() -> None:
    with pytest.raises(ArchiveError, match="negative"):
        plan_archive([f("SKILL.md", size=-1)])


def test_accepts_a_zero_byte_file() -> None:
    # An empty .gitkeep, __init__.py, or py.typed is ordinary in a real skill tree —
    # zero must not be treated as "negative".
    assert [e.path for e in plan_archive([f("SKILL.md", size=0)])] == ["SKILL.md"]


def test_refuses_a_tree_with_no_plugin_marker() -> None:
    with pytest.raises(ArchiveLayoutError, match="no plugin content"):
        plan_archive([f("README.md")])


def test_layout_error_reports_what_was_actually_found_at_the_root() -> None:
    with pytest.raises(ArchiveLayoutError) as excinfo:
        plan_archive([f("README.md"), f("docs/readme.md")])
    message = str(excinfo.value)
    assert "root directories found: docs" in message
    assert "root files found: README.md" in message


def test_layout_error_says_the_marker_list_itself_may_be_the_stale_thing() -> None:
    """The refusal must not present its own marker list as authoritative.

    `PLUGIN_MARKER_DIRS` goes stale *silently* when the client's accepted layout
    changes: a correct skill is then refused by a message that reads as if the artifact
    were at fault. The submitter concludes their skill is malformed, works around the
    gate, and installs by hand — so the fleet keeps a front door that certifies nothing.

    Asserted with `endswith` rather than a substring because this clause has to be the
    last thing read, after the marker lists it is qualifying. Every other test here
    matches on the `"no plugin content"` prefix, so without this one the whole clause
    could be deleted and the suite would stay green.
    """
    with pytest.raises(ArchiveLayoutError) as excinfo:
        plan_archive([f("README.md")])
    assert str(excinfo.value).endswith(
        " — if this layout looks correct, the marker list above may be stale: re-verify it"
        " against the intake spec (or the client) before assuming the artifact is at fault"
    )


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


# --- one level inside a single wrapper directory (the shape `git archive` produces) ---


def test_accepts_a_marker_one_level_inside_a_single_wrapper_directory() -> None:
    planned = plan_archive([f("my-skill-1.0/skills/foo.md")])
    assert [e.path for e in planned] == ["my-skill-1.0/skills/foo.md"]


def test_accepts_a_bare_skill_md_inside_a_single_wrapper_directory() -> None:
    planned = plan_archive([f("my-skill-1.0/SKILL.md")])
    assert [e.path for e in planned] == ["my-skill-1.0/SKILL.md"]


def test_refuses_a_marker_two_levels_deep() -> None:
    with pytest.raises(ArchiveLayoutError, match="no plugin content"):
        plan_archive([f("a/b/skills/foo.md")])


def test_wrapper_allowance_requires_a_single_shared_root() -> None:
    # Two different top-level directories means there is no single wrapper to strip,
    # so a marker one level inside either of them must not be inferred.
    with pytest.raises(ArchiveLayoutError, match="no plugin content"):
        plan_archive([f("a/skills/foo.md"), f("b/readme.md")])


# --- every marker is individually pinned, so dropping any one is caught here ---


@pytest.mark.parametrize(
    "directory",
    [
        ".claude-plugin",
        "commands",
        "skills",
        "agents",
        "hooks",
        "themes",
        "output-styles",
        "monitors",
        "workflows",
    ],
)
def test_recognises_each_directory_marker(directory: str) -> None:
    planned = plan_archive([f(f"{directory}/x.md")])
    assert [e.path for e in planned] == [f"{directory}/x.md"]


@pytest.mark.parametrize("filename", ["SKILL.md", ".mcp.json", ".lsp.json"])
def test_recognises_each_file_marker(filename: str) -> None:
    assert [e.path for e in plan_archive([f(filename)])] == [filename]


def test_marker_tuples_have_not_grown_or_shrunk() -> None:
    # Belt-and-suspenders alongside the parametrized tests above: this fails the moment
    # PLUGIN_MARKER_DIRS or PLUGIN_MARKER_FILES gains or loses an entry, even one this
    # test file's own parametrize lists were not updated to match.
    assert set(PLUGIN_MARKER_DIRS) == {
        ".claude-plugin",
        "commands",
        "skills",
        "agents",
        "hooks",
        "themes",
        "output-styles",
        "monitors",
        "workflows",
    }
    assert set(PLUGIN_MARKER_FILES) == {"SKILL.md", ".mcp.json", ".lsp.json"}
