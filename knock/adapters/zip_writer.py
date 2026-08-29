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

`entries` must already be the trusted output of `plan_archive` (or equivalent
validation) — this function does not re-check for symlinks, path traversal, or size. It
reads `root / entry.path` and writes it under `entry.path` verbatim; feeding it an
unvalidated list reintroduces the zip-slip the planner exists to prevent.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from knock.domain.packaging import ArchiveEntry

# The zip epoch. Any constant works; this one is the format's own minimum.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def write_archive(root: Path, entries: list[ArchiveEntry], destination: Path) -> None:
    """Write `entries` (already ordered and validated) from `root` into `destination`.

    Trust boundary: `entries` is assumed to be the trusted output of a planner such as
    `plan_archive` — every path is already relative, root-confined, and not a symlink.
    """
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            info = zipfile.ZipInfo(filename=entry.path, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so external_attr below is read as a POSIX mode
            info.external_attr = (0o100000 | entry.mode) << 16
            zf.writestr(info, (root / entry.path).read_bytes())
