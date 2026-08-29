"""Write a byte-reproducible zip from a planned entry list.

Reproducibility comes from writing an explicit `ZipInfo` per entry rather than letting
zipfile read the filesystem: the timestamp is a constant, so no clock and no TZ can leak
into the bytes, and the digest becomes a pure function of the content, the plan, the
zlib implementation, and the compression level. `create_system` is likewise pinned to
Unix regardless of the host platform, so the mode bits stored in `external_attr` are
interpreted the same way by any reader that honours them — that includes `unzip(1)` and
most archive managers, but notably NOT Python's own `zipfile.ZipFile.extract`/
`extractall`, which never calls `chmod` on what it writes. The mode is correctly
*stored*; whether it is *applied* depends on the extractor a consumer uses downstream.

Things this module cannot guarantee on its own:
- **Cross-machine byte-identity depends on the zlib build.** `ZIP_DEFLATED` output is
  deterministic for a fixed zlib version and compression level, but different zlib
  builds (different versions, or vendor patches such as zlib-ng) are not guaranteed to
  produce identical compressed bytes for the same input, even though they decompress to
  the same content. Reproducibility of the OCI blob digest across machines therefore
  also depends on pinning the zlib the packaging step runs against (e.g. one controlled
  container image), not just on what this module does.
- **The compression level is currently unpinned, and cannot be pinned through the
  obvious API.** No `compresslevel` is passed anywhere here, so zlib uses its own
  default (`Z_DEFAULT_COMPRESSION`, typically level 6). `zipfile.ZipFile.writestr`
  reads the level from `ZipInfo.compress_level`, never from a `compresslevel` passed to
  the `ZipFile` constructor — so the seemingly obvious future hardening of adding
  `compresslevel=` to the `zipfile.ZipFile(...)` call below would be silently ignored.
  Pinning the level requires setting `info.compress_level` on each `ZipInfo` instead.
- **The 100 MiB bound is enforced by the planner, not here.** `entries` are read fully
  into memory one at a time via `Path.read_bytes()`; that is safe only because
  `knock.domain.packaging.plan_archive` already rejected any tree over
  `MAX_ARCHIVE_BYTES` before this function ever sees the entry list.

`entries` is expected to be the trusted output of `plan_archive` (or equivalent
validation), but this adapter is the only component in the repository that actually
touches the filesystem — `plan_archive` only ever sees descriptions of a tree, never
the tree itself — so it re-derives, and re-checks, everything it is able to rather than
trusting the plan on faith:
- `entry.path` is re-checked with `path_escapes_root` (root escape, and non-canonical
  or duplicate arcnames), raising `ArchiveError`.
- Each source is checked with `is_symlink()` before it is read, raising `ArchiveError`.
  A hand-built `ArchiveEntry` that points at a symlink is refused outright, not read
  through to whatever it targets.
- Each source's actual byte count is compared against `entry.size` after reading,
  raising `ArchiveSizeMismatchError` on a change in byte count. This is a
  concurrent-modification detector, not a content check: a same-length substitution
  (the file changed without changing size) is undetectable here and always will be —
  that needs a content hash carried from planning time, which is out of scope for this
  module.
- The write itself is all-or-nothing: entries are written to a temporary file next to
  `destination` and only `os.replace`d into place once every entry has succeeded. Any
  failure — including one this module cannot classify, wrapped as
  `ArchiveDestinationWriteError` — leaves `destination` exactly as it was found (absent,
  or its previous contents), never a well-formed archive of a subset of the tree.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from knock.domain.packaging import ArchiveEntry, path_escapes_root
from knock.errors import (
    ArchiveDestinationWriteError,
    ArchiveError,
    ArchiveSizeMismatchError,
    ArchiveSourceReadError,
)

# The zip epoch is (1980, 1, 1); sitting a couple of days above that floor gives any
# future arithmetic on this constant (e.g. "one day earlier") headroom before it
# underflows a format that cannot represent an earlier date at all.
FIXED_DATE_TIME = (1980, 1, 3, 0, 0, 0)


def _write_entries(root: Path, entries: list[ArchiveEntry], tmp_path: Path) -> None:
    seen: set[str] = set()
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            if path_escapes_root(entry.path):
                raise ArchiveError(f"refusing to write a path that escapes the root: {entry.path}")
            if entry.path in seen:
                raise ArchiveError(f"refusing a duplicate archive path: {entry.path}")
            seen.add(entry.path)

            source = root / entry.path
            if source.is_symlink():
                raise ArchiveError(f"refusing to write a symlink: {entry.path}")
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise ArchiveSourceReadError(
                    f"could not read source file for archive entry {entry.path!r}: {source}"
                ) from exc
            if len(data) != entry.size:
                raise ArchiveSizeMismatchError(
                    f"archive entry {entry.path!r} was planned at {entry.size} bytes "
                    f"but read back as {len(data)} bytes: {source}"
                )

            info = zipfile.ZipInfo(filename=entry.path, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so external_attr below is read as a POSIX mode
            info.external_attr = (0o100000 | entry.mode) << 16
            zf.writestr(info, data)


def write_archive(root: Path, entries: list[ArchiveEntry], destination: Path) -> None:
    """Write `entries` (already ordered and validated) from `root` into `destination`.

    Trust boundary: `entries` is expected to be the output of a planner such as
    `plan_archive` — every path already relative, root-confined, canonical, and not a
    symlink. This adapter re-verifies all of that itself (see the module docstring for
    the full list) rather than trusting the plan, because it is the only component that
    can: it is the sole place in the repository with a real filesystem to check against.

    The write is atomic: entries are written to a temporary file created alongside
    `destination` (so the final `os.replace` is same-filesystem and atomic on POSIX),
    and `destination` is only ever touched by that final rename, once every entry has
    been written successfully. On any failure — `ArchiveError` for an unsafe or
    malformed entry, `ArchiveSourceReadError` for a source that vanished or became
    unreadable, `ArchiveSizeMismatchError` for one that changed size underneath the
    write, or `ArchiveDestinationWriteError` for a fault on the destination side
    (unwritable directory, destination is itself a directory, disk full) — the
    temporary file is removed and `destination` is left exactly as it was found: never
    a partial archive, and never a good prior archive clobbered by a failed rebuild.
    """
    destination = Path(destination)
    tmp_name: str | None = None
    completed = False
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
        os.close(fd)
        _write_entries(root, entries, Path(tmp_name))
        os.replace(tmp_name, destination)
        completed = True
    except OSError as exc:
        raise ArchiveDestinationWriteError(
            f"could not write the archive to {destination}: {exc}"
        ) from exc
    finally:
        if tmp_name is not None and not completed:
            Path(tmp_name).unlink(missing_ok=True)
