"""Write a byte-reproducible zip from a planned entry list.

Reproducibility comes from writing an explicit `ZipInfo` per entry rather than letting
zipfile read the filesystem: the timestamp is a constant, so no clock and no TZ can leak
into the bytes, and the digest becomes a pure function of the content plus the plan.
`create_system` is likewise pinned to Unix regardless of the host platform, so the mode
bits stored in `external_attr` are interpreted the same way by any reader that honours
them — that includes `unzip(1)` and most archive managers, but notably NOT Python's own
`zipfile.ZipFile.extract`/`extractall`, which never calls `chmod` on what it writes. The
mode is correctly *stored*; whether it is *applied* depends on the extractor a consumer
uses downstream.

Two things this module cannot guarantee on its own:
- **Cross-machine byte-identity depends on the zlib build.** `ZIP_DEFLATED` output is
  deterministic for a fixed zlib version and compression level, but different zlib
  builds (different versions, or vendor patches such as zlib-ng) are not guaranteed to
  produce identical compressed bytes for the same input, even though they decompress to
  the same content. Reproducibility of the OCI blob digest across machines therefore
  also depends on pinning the zlib the packaging step runs against (e.g. one controlled
  container image), not just on what this module does.
- **The 100 MiB bound is enforced by the planner, not here.** `entries` are read fully
  into memory one at a time via `Path.read_bytes()`; that is safe only because
  `knock.domain.packaging.plan_archive` already rejected any tree over
  `MAX_ARCHIVE_BYTES` before this function ever sees the entry list.

`entries` is expected to be the trusted output of `plan_archive` (or equivalent
validation), but the adapter does not simply trust that on faith: every `entry.path` is
re-checked with `path_escapes_root` before it is written, so a caller that builds
`ArchiveEntry` values by hand cannot smuggle a root-escaping arcname past this boundary
just because it skipped the planner. What the adapter cannot re-derive from `entries`
alone — a symlink, or content that changed after `plan_archive` measured it — is instead
caught as it happens: a source file that has vanished or become unreadable since
planning raises `ArchiveSourceReadError`, and one whose byte count no longer matches the
size `plan_archive` recorded raises `ArchiveSizeMismatchError`, rather than silently
shipping a partial file under a clean, stable digest.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from knock.domain.packaging import ArchiveEntry, path_escapes_root
from knock.errors import ArchiveError, ArchiveSizeMismatchError, ArchiveSourceReadError

# The zip epoch is (1980, 1, 1); sitting a couple of days above that floor gives any
# future arithmetic on this constant (e.g. "one day earlier") headroom before it
# underflows a format that cannot represent an earlier date at all.
FIXED_DATE_TIME = (1980, 1, 3, 0, 0, 0)


def write_archive(root: Path, entries: list[ArchiveEntry], destination: Path) -> None:
    """Write `entries` (already ordered and validated) from `root` into `destination`.

    Trust boundary: `entries` is expected to be the output of a planner such as
    `plan_archive` — every path already relative, root-confined, and not a symlink.
    Each `entry.path` is still re-checked here with `path_escapes_root` (raising
    `ArchiveError` on a hit), so the adapter stays safe even if a caller bypasses the
    planner; what it cannot independently verify — that the source file still exists,
    is readable, and still matches the size recorded at planning time — is checked
    against the filesystem as each entry is written, raising `ArchiveSourceReadError` or
    `ArchiveSizeMismatchError` respectively rather than writing a silently partial file.
    """
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            if path_escapes_root(entry.path):
                raise ArchiveError(f"refusing to write a path that escapes the root: {entry.path}")
            source = root / entry.path
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
