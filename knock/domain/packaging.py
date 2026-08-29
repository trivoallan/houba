"""Pure packaging decisions for a non-image artifact. No I/O.

The caller describes the tree; this module decides what is packaged, in what order, and
refuses what must never enter an archive. Keeping it pure is what makes every refusal
testable without a filesystem.

    tree description ──▶ plan_archive ──▶ ordered entries
                              │
                              ├── symlink?        ──▶ ArchiveError
                              ├── path escapes?   ──▶ ArchiveError
                              ├── over the bound? ──▶ ArchiveError
                              └── no marker?      ──▶ ArchiveError
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from knock.errors import ArchiveError

__all__ = [
    "MAX_ARCHIVE_BYTES",
    "PLUGIN_MARKERS",
    "ArchiveEntry",
    "SourceFile",
    "plan_archive",
]

# SkillSpector caps ingestion at 100 MiB; an artifact it cannot read can never be judged,
# so an artifact larger than this could never pass the gate. Bounding here makes that
# failure happen at intake, where a human is already looking, instead of on a workstation.
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024

# The client accepts a plugin whose root holds `.claude-plugin/` or any one of these.
PLUGIN_MARKERS = (
    ".claude-plugin",
    "commands",
    "skills",
    "agents",
    "hooks",
    "themes",
    "output-styles",
    "monitors",
    "workflows",
    "SKILL.md",
    ".mcp.json",
    ".lsp.json",
)


@dataclass(frozen=True)
class SourceFile:
    """One file as the caller found it on disk. `path` is relative to the tree root."""

    path: str
    size: int
    is_symlink: bool = False
    is_executable: bool = False


@dataclass(frozen=True)
class ArchiveEntry:
    """One planned archive member: where it goes and the mode it is stored with."""

    path: str
    mode: int


def _escapes(path: str) -> bool:
    """True if `path` is unsafe to extract underneath the tree root.

    Rejects absolute paths, parent-directory traversal (checked after normalisation, so
    `a/../../b` is caught even though no single segment reads `..` before it), the empty
    path, and any path that normalises to the root itself (`.`) — none of those name a
    real file inside the tree.

    Also rejects any backslash. `posixpath` treats `\\` as an ordinary filename
    character, so `..\\evil` normalises to the single opaque segment `..\\evil` and
    passes every check above — but a consumer that later extracts the archive on
    Windows treats `\\` as a path separator, so that "filename" is `..` followed by
    `evil` and walks out of the root there. Refusing any backslash here closes that gap
    without needing to know what will extract the archive.
    """
    if not path or "\\" in path:
        return True
    if posixpath.isabs(path):
        return True
    normalised = posixpath.normpath(path)
    return normalised in (".", "..") or normalised.startswith("../")


def _has_marker(paths: list[str]) -> bool:
    roots = {p.split("/", 1)[0] for p in paths}
    return any(marker in roots for marker in PLUGIN_MARKERS)


def plan_archive(
    files: list[SourceFile], *, max_bytes: int = MAX_ARCHIVE_BYTES
) -> list[ArchiveEntry]:
    """Order and validate the archive members, or raise ArchiveError.

    Walks `files` once, refusing the first symlink or root-escaping path it meets (per
    file, a symlink is caught before its path is even checked for escaping). Once every
    file has cleared that pass, the declared sizes are summed and compared against
    `max_bytes`, and only then is the tree checked for a plugin marker at its root.
    """
    total = 0
    for file in files:
        if file.is_symlink:
            raise ArchiveError(f"refusing to package a symlink: {file.path}")
        if _escapes(file.path):
            raise ArchiveError(f"refusing to package a path that escapes the root: {file.path}")
        total += file.size
    if total > max_bytes:
        raise ArchiveError(f"tree exceeds the archive bound: {total} > {max_bytes} bytes")
    paths = [f.path for f in files]
    if not _has_marker(paths):
        raise ArchiveError(
            "no plugin content at the root (expected one of: " + ", ".join(PLUGIN_MARKERS) + ")"
        )
    return [
        ArchiveEntry(path=file.path, mode=0o755 if file.is_executable else 0o644)
        for file in sorted(files, key=lambda f: f.path)
    ]
