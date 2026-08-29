"""The same tree must always produce the same bytes."""

from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
from pathlib import Path

import pytest

from knock.adapters.zip_writer import write_archive
from knock.domain.packaging import ArchiveEntry
from knock.errors import (
    ArchiveDestinationWriteError,
    ArchiveError,
    ArchiveSizeMismatchError,
    ArchiveSourceReadError,
    exit_code_for,
)

PLUGIN_JSON = '{"name":"probe"}'
RUN_SH = "#!/bin/sh\necho hi\n"

RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _tree(root: Path) -> None:
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(PLUGIN_JSON)
    (root / "scripts").mkdir()
    (root / "scripts" / "run.sh").write_text(RUN_SH)


ENTRIES = [
    ArchiveEntry(path=".claude-plugin/plugin.json", mode=0o644, size=len(PLUGIN_JSON.encode())),
    ArchiveEntry(path="scripts/run.sh", mode=0o755, size=len(RUN_SH.encode())),
]


def test_same_tree_yields_identical_bytes(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    first = tmp_path / "a.zip"
    second = tmp_path / "b.zip"
    write_archive(root, ENTRIES, first)
    write_archive(root, ENTRIES, second)
    assert hashlib.sha256(first.read_bytes()).hexdigest() == (
        hashlib.sha256(second.read_bytes()).hexdigest()
    )


def test_mtime_does_not_leak_into_the_archive(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    baseline = tmp_path / "a.zip"
    write_archive(root, ENTRIES, baseline)

    os.utime(root / "scripts" / "run.sh", (1, 1))
    later = tmp_path / "b.zip"
    write_archive(root, ENTRIES, later)
    assert baseline.read_bytes() == later.read_bytes()


def test_umask_does_not_leak_into_the_archive(tmp_path: Path) -> None:
    """The mode comes from `ArchiveEntry.mode`, never from a `chmod` on the destination
    file, so the process umask must have no effect on the archive bytes."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    permissive = tmp_path / "a.zip"
    restrictive = tmp_path / "b.zip"
    old_umask = os.umask(0o000)
    try:
        write_archive(root, ENTRIES, permissive)
        os.umask(0o077)
        write_archive(root, ENTRIES, restrictive)
    finally:
        os.umask(old_umask)
    assert permissive.read_bytes() == restrictive.read_bytes()


def test_destination_path_does_not_leak_into_the_archive(tmp_path: Path) -> None:
    """The archive's own filename or location is not archive content, so writing the
    same entries to differently named/nested destinations must yield identical bytes."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    short = tmp_path / "a.zip"
    (tmp_path / "nested" / "deeply").mkdir(parents=True)
    long = tmp_path / "nested" / "deeply" / "a-very-different-name.zip"
    write_archive(root, ENTRIES, short)
    write_archive(root, ENTRIES, long)
    assert short.read_bytes() == long.read_bytes()


def test_working_directory_does_not_leak_into_the_archive(tmp_path: Path) -> None:
    """`root` and `destination` are always passed as absolute paths by the caller; this
    confirms the writer itself does not implicitly depend on the process cwd."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    baseline = tmp_path / "a.zip"
    write_archive(root, ENTRIES, baseline)

    cwd = os.getcwd()
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.chdir(other)
    try:
        from_elsewhere = tmp_path / "b.zip"
        write_archive(root, ENTRIES, from_elsewhere)
    finally:
        os.chdir(cwd)
    assert baseline.read_bytes() == from_elsewhere.read_bytes()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset is not available on this platform")
def test_timezone_does_not_leak_into_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FIXED_DATE_TIME` exists precisely so the ambient timezone cannot leak into the
    archive. Build the same input under two different `TZ` values and confirm that."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    first = tmp_path / "a.zip"
    write_archive(root, ENTRIES, first)

    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # UTC+14, about as far as TZ offsets go
    time.tzset()
    second = tmp_path / "b.zip"
    write_archive(root, ENTRIES, second)

    monkeypatch.delenv("TZ", raising=False)
    time.tzset()
    assert first.read_bytes() == second.read_bytes()


def test_matches_a_golden_digest(tmp_path: Path) -> None:
    """Pins the actual output, not just its stability. Every other test in this file
    proves the digest doesn't move under some irrelevant factor; this one proves it
    doesn't move at all — a refactor that changes byte layout (a different `ZipInfo`
    field order, a different constant, a different compression choice) fails this test
    even though it might pass every other one here.

    If this test ever needs updating, that is a deliberate, visible decision about the
    archive format changing — never a side effect of an unrelated refactor. If it fails
    alongside `test_golden_archive_structure` below, that is a real regression; if it
    fails alone (structure test still green), that points at the zlib build instead —
    see the module docstring's reproducibility caveats.
    """
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    out = tmp_path / "a.zip"
    write_archive(root, ENTRIES, out)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == (
        "05c3e714ddc0bb1924da614288c062e1fa907fd842486400225d1c004bcbd139"
    )


def test_golden_archive_structure(tmp_path: Path) -> None:
    """Structural companion to `test_matches_a_golden_digest`: pins the archive's
    metadata directly, independent of zlib's compressed bytes, so a real format
    regression (wrong field, wrong order, wrong constant) can be told apart from a
    digest drift caused by the zlib build producing different compressed bytes for the
    same content."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    out = tmp_path / "a.zip"
    write_archive(root, ENTRIES, out)
    with zipfile.ZipFile(out) as zf:
        infos = zf.infolist()

    assert [i.filename for i in infos] == [".claude-plugin/plugin.json", "scripts/run.sh"]
    assert [i.file_size for i in infos] == [len(PLUGIN_JSON.encode()), len(RUN_SH.encode())]
    assert [i.compress_type for i in infos] == [zipfile.ZIP_DEFLATED, zipfile.ZIP_DEFLATED]
    assert [i.create_system for i in infos] == [3, 3]
    assert [i.date_time for i in infos] == [(1980, 1, 3, 0, 0, 0), (1980, 1, 3, 0, 0, 0)]
    assert [i.external_attr >> 16 for i in infos] == [0o100644, 0o100755]


def test_create_system_is_pinned_to_unix(tmp_path: Path) -> None:
    """`create_system` controls how a reader interprets `external_attr`. Pinning it to 3
    (Unix) regardless of the host platform is what makes the stored mode bits mean the
    same thing wherever the archive is built or read."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    out = tmp_path / "a.zip"
    write_archive(root, ENTRIES, out)
    with zipfile.ZipFile(out) as zf:
        infos = zf.infolist()
    assert all(i.create_system == 3 for i in infos)


def test_entry_order_and_modes_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    out = tmp_path / "a.zip"
    write_archive(root, ENTRIES, out)
    with zipfile.ZipFile(out) as zf:
        infos = zf.infolist()
    assert [i.filename for i in infos] == [".claude-plugin/plugin.json", "scripts/run.sh"]
    assert infos[1].external_attr >> 16 == 0o100755
    assert all(i.date_time == (1980, 1, 3, 0, 0, 0) for i in infos)


def test_missing_source_file_raises_archive_source_read_error(tmp_path: Path) -> None:
    """A vanished, unreadable, or dangling-symlink source must not surface as a raw
    `OSError` — that skips every exit-code handler in `main.py` and reads to an operator
    as an unhandled crash rather than the filesystem fault it is."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    (root / "scripts" / "run.sh").unlink()  # planned, but gone by the time we write
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveSourceReadError, match=re.escape("scripts/run.sh")):
        write_archive(root, ENTRIES, out)


def test_source_read_error_exits_2() -> None:
    """`ArchiveSourceReadError` is rooted at `AdapterError` — a filesystem fault, exit 2
    — not at `DomainError` (exit 1, "your input is invalid") or the `InternalError`
    fallback (exit 4, "bug"). Pinning this in a test is what the review specifically
    asked for: the exit code is part of the contract, not an implementation detail."""
    assert exit_code_for(ArchiveSourceReadError("boom")) == 2


def test_truncated_source_file_raises_archive_size_mismatch_error(tmp_path: Path) -> None:
    """If a source file's byte count no longer matches what `plan_archive` recorded for
    it — the tree changed underneath the packaging step — writing must refuse rather
    than silently produce a well-formed, deterministically-digested archive of a
    partial file, which is indistinguishable downstream from a complete one."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    (root / "scripts" / "run.sh").write_text("#!/bin/sh\n")  # shorter than planned
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveSizeMismatchError, match=re.escape("scripts/run.sh")):
        write_archive(root, ENTRIES, out)


def test_grown_source_file_raises_archive_size_mismatch_error(tmp_path: Path) -> None:
    """The mirror image of the truncation test: a file that grew past what
    `plan_archive` measured is just as much a change in byte count as one that shrank,
    and matters more — appended content is exactly how an attacker would smuggle a
    payload into an already-planned entry. The check must be `!=`, not `<`: a mutation
    that weakens it to `<` (only "smaller than planned" refused) would silently accept
    this case, and this test exists specifically to make that mutation observable."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    (root / "scripts" / "run.sh").write_text(RUN_SH + "echo SMUGGLED\n")  # longer than planned
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveSizeMismatchError, match=re.escape("scripts/run.sh")):
        write_archive(root, ENTRIES, out)


def test_size_mismatch_error_exits_2() -> None:
    """A concurrent-modification race detected at the write boundary, not a bug and not
    bad input: `ArchiveSizeMismatchError` is rooted at `AdapterError` (exit 2) because
    the remedy is environmental — re-run against a quiescent tree — not because the
    adapter itself misbehaved. See its docstring in `knock/errors.py`."""
    assert exit_code_for(ArchiveSizeMismatchError("boom")) == 2


def test_same_length_content_substitution_is_not_detected(tmp_path: Path) -> None:
    """Documents a real, deliberate scope boundary rather than leaving it as an
    unverified docstring claim: a byte-count check cannot catch a same-length
    substitution (the file's bytes changed but its length didn't). Catching that would
    need a content hash carried from planning time, which is out of scope for this
    module. This test exists so the boundary is visible and intentional, not an
    accidental gap nobody checked."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    substituted = "#!/bin/sh\necho HI\n"  # same length as RUN_SH, different content
    assert len(substituted.encode()) == len(RUN_SH.encode())
    (root / "scripts" / "run.sh").write_text(substituted)
    out = tmp_path / "a.zip"
    write_archive(root, ENTRIES, out)  # does not raise
    with zipfile.ZipFile(out) as zf:
        assert zf.read("scripts/run.sh") == substituted.encode()


def test_write_archive_refuses_a_root_escaping_entry_even_without_the_planner(
    tmp_path: Path,
) -> None:
    """`plan_archive`'s traversal check does not make `write_archive` safe on its own —
    the adapter is separately reachable by any caller that builds `ArchiveEntry` values
    by hand. This writes straight to `write_archive`, bypassing `plan_archive` entirely,
    with an arcname that would zip-slip out of the extraction root."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    escaping = [ArchiveEntry(path="../evil", mode=0o644, size=0)]
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveError, match="escapes"):
        write_archive(root, escaping, out)
    # The write is atomic: a refused entry must never leave anything at all behind,
    # good or partial, at the destination path.
    assert not out.exists()


def test_write_archive_refuses_a_non_canonical_arcname_even_without_the_planner(
    tmp_path: Path,
) -> None:
    """A hand-built entry can also carry a non-canonical arcname (`a/../b`) that never
    goes through `plan_archive`'s sorting/canonicalisation. It doesn't escape the root,
    but it breaks the "canonical, sorted entries" premise reproducibility rests on, so
    the write boundary must refuse it independently, same as the escape case."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    non_canonical = [
        ArchiveEntry(path="scripts/../scripts/run.sh", mode=0o755, size=len(RUN_SH.encode()))
    ]
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveError, match="escapes"):
        write_archive(root, non_canonical, out)
    assert not out.exists()


def test_write_archive_refuses_a_duplicate_path(tmp_path: Path) -> None:
    """Two hand-built entries naming the same archive path would silently produce two
    same-named zip members (the stdlib only warns) — refuse it outright instead, for
    the same reason a non-canonical arcname is refused: reproducibility depends on
    every entry appearing exactly once at its one canonical spelling."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    duplicated = [
        ArchiveEntry(path="scripts/run.sh", mode=0o755, size=len(RUN_SH.encode())),
        ArchiveEntry(path="scripts/run.sh", mode=0o644, size=len(RUN_SH.encode())),
    ]
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveError, match="duplicate"):
        write_archive(root, duplicated, out)
    assert not out.exists()


def test_write_archive_refuses_a_symlinked_source_even_without_the_planner(
    tmp_path: Path,
) -> None:
    """`path_escapes_root` only judges the *arcname* — where the entry claims to live in
    the archive. It says nothing about whether the *source* on disk is a symlink, so a
    hand-built entry with an innocent-looking arcname can still point through a symlink
    at content the source tree never contained. This is `write_archive` the only
    component in the repository with a real filesystem to check — refusing that read
    outright, before the target's bytes are ever published under the skill's label."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    secret = tmp_path / "outside_root_secret.txt"
    secret.write_text("TOP SECRET OUTSIDE ROOT\n")
    (root / "scripts" / "note.md").symlink_to(secret)

    smuggled = [ArchiveEntry(path="scripts/note.md", mode=0o644, size=secret.stat().st_size)]
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveError, match="symlink"):
        write_archive(root, smuggled, out)
    assert not out.exists()


def test_destination_is_a_directory_raises_archive_destination_write_error(
    tmp_path: Path,
) -> None:
    """A destination-side fault (here, the destination path is itself an existing
    directory) must not surface as a raw `OSError` any more than a source-side one
    should — same defect class as `ArchiveSourceReadError`, just on the output side."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    out = tmp_path / "already_a_directory"
    out.mkdir()
    with pytest.raises(ArchiveDestinationWriteError):
        write_archive(root, ENTRIES, out)
    # Nothing was clobbered: the path is still exactly what it was before.
    assert out.is_dir()
    assert list(out.iterdir()) == []


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="permission bits do not restrict root")
def test_unwritable_destination_directory_raises_archive_destination_write_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    os.chmod(readonly_dir, 0o555)
    try:
        with pytest.raises(ArchiveDestinationWriteError):
            write_archive(root, ENTRIES, readonly_dir / "out.zip")
    finally:
        os.chmod(readonly_dir, 0o755)  # so pytest can clean up tmp_path afterward


def test_destination_write_error_exits_2() -> None:
    """Same reasoning as `test_source_read_error_exits_2`, mirrored on the output side:
    a filesystem fault while writing the destination is `AdapterError`, exit 2."""
    assert exit_code_for(ArchiveDestinationWriteError("boom")) == 2


def test_failed_write_does_not_leave_a_partial_archive_at_the_destination(
    tmp_path: Path,
) -> None:
    """Every failure path — not just the zip-slip case above — must leave `destination`
    untouched. A well-formed archive of a subset of the tree is exactly the defect this
    module exists to prevent, whether the cause is a rejected entry or, as here, a
    source file that vanished mid-write."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    (root / "scripts" / "run.sh").unlink()
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveSourceReadError):
        write_archive(root, ENTRIES, out)
    assert not out.exists()
    # And nothing named like a temp artifact was left behind in the destination
    # directory either.
    assert list(tmp_path.glob(".a.zip.*.tmp")) == []


def test_failed_rebuild_does_not_clobber_a_prior_good_archive(tmp_path: Path) -> None:
    """The most important case for I-1: a rebuild that fails partway through must not
    destroy a previously-published, correct archive at the same path. "Build succeeds,
    then a later rebuild fails" must leave the last good artifact standing, not replace
    it with a well-formed archive of a subset of the tree."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    out = tmp_path / "a.zip"
    write_archive(root, ENTRIES, out)
    good_bytes = out.read_bytes()

    (root / "scripts" / "run.sh").unlink()  # the rebuild will fail
    with pytest.raises(ArchiveSourceReadError):
        write_archive(root, ENTRIES, out)

    assert out.read_bytes() == good_bytes
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == [".claude-plugin/plugin.json", "scripts/run.sh"]
