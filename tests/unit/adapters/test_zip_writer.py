"""The same tree must always produce the same bytes."""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

from knock.adapters.zip_writer import write_archive
from knock.domain.packaging import ArchiveEntry


def _tree(root: Path) -> None:
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text('{"name":"probe"}')
    (root / "scripts").mkdir()
    (root / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n")


ENTRIES = [
    ArchiveEntry(path=".claude-plugin/plugin.json", mode=0o644),
    ArchiveEntry(path="scripts/run.sh", mode=0o755),
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
    assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in infos)
