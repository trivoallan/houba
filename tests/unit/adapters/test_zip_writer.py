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
    ArchiveError,
    ArchiveSizeMismatchError,
    ArchiveSourceReadError,
    exit_code_for,
)

PLUGIN_JSON = '{"name":"probe"}'
RUN_SH = "#!/bin/sh\necho hi\n"


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
    archive format changing — never a side effect of an unrelated refactor.
    """
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    out = tmp_path / "a.zip"
    write_archive(root, ENTRIES, out)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == (
        "05c3e714ddc0bb1924da614288c062e1fa907fd842486400225d1c004bcbd139"
    )


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
    """If a source file no longer matches the size `plan_archive` recorded for it — the
    tree changed underneath the packaging step — writing must refuse rather than
    silently produce a well-formed, deterministically-digested archive of a partial
    file, which is indistinguishable downstream from a complete one."""
    root = tmp_path / "src"
    root.mkdir()
    _tree(root)
    (root / "scripts" / "run.sh").write_text("#!/bin/sh\n")  # shorter than planned
    out = tmp_path / "a.zip"
    with pytest.raises(ArchiveSizeMismatchError, match=re.escape("scripts/run.sh")):
        write_archive(root, ENTRIES, out)


def test_size_mismatch_error_exits_2() -> None:
    """Same reasoning as `test_source_read_error_exits_2`: this is a filesystem-level
    integrity fault discovered while writing, not a bad input to reject at exit 1."""
    assert exit_code_for(ArchiveSizeMismatchError("boom")) == 2


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
    # The escaping arcname is refused before it (or anything after it) reaches the zip.
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == []
