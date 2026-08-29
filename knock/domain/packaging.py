"""Pure packaging decisions for a non-image artifact. No I/O.

The caller describes the tree; this module decides what is packaged, in what order, and
refuses what must never enter an archive. Keeping it pure is what makes every refusal
testable without a filesystem.

    tree description ──▶ plan_archive ──▶ ordered entries
                              │
                              ├── negative size?      ──▶ ArchiveError (caller bug)
                              ├── symlinks?           ──▶ ArchiveError (accumulated)
                              ├── backslash paths?    ──▶ ArchiveError (accumulated)
                              ├── paths escape root?  ──▶ ArchiveError (accumulated)
                              ├── paths collide?      ──▶ ArchiveError (accumulated)
                              ├── over the bound?     ──▶ ArchiveError
                              └── no plugin marker?   ──▶ ArchiveLayoutError
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from knock.errors import ArchiveError, ArchiveLayoutError

__all__ = [
    "MAX_ARCHIVE_BYTES",
    "PLUGIN_MARKER_DIRS",
    "PLUGIN_MARKER_FILES",
    "ArchiveEntry",
    "SourceFile",
    "plan_archive",
]

# SkillSpector caps ingestion at `INGEST_MAX_BYTES` (100 MiB); an artifact it cannot read
# can never be judged, so an artifact larger than this could never pass the gate. Bounding
# here makes that failure happen at intake, where a human is already looking, instead of
# on a workstation.
#
# `INGEST_MAX_BYTES` applies to a zip archive's total *uncompressed* size, so summing the
# declared (uncompressed) source sizes below is exact, not a conservative estimate — there
# is no compression-ratio slack to reason about when tuning this bound.
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024

# The client (Claude Code) accepts a plugin whose root holds `.claude-plugin/` or one of
# the entries below — verified against Claude Code 2.1.251, recorded in
# `docs/superpowers/specs/2026-08-29-external-skill-intake-design.md` ("Verified — the
# client contract (2026-08-29)", and the packaging error-handling table further down that
# same spec). That spec is this list's source of truth: if the client's accepted root
# layout ever changes, this tuple goes stale *silently* — a correctly-formed skill would
# be refused at intake with a message that lists these strings and gives no hint that the
# list itself, not the skill, is what's wrong. Re-verify against the spec (or the client
# directly) before trusting this list after a Claude Code upgrade.
#
# Split by kind because `plan_archive` receives files only, never directories (see
# `SourceFile`) — a root segment can only be classified as a directory-marker candidate
# if some listed path has more path components under it. Matching is case-sensitive: the
# client only ever runs on Linux in this fleet, so no case-insensitive fallback is needed.
PLUGIN_MARKER_DIRS = (
    ".claude-plugin",
    "commands",
    "skills",
    "agents",
    "hooks",
    "themes",
    "output-styles",
    "monitors",
    "workflows",
)
PLUGIN_MARKER_FILES = (
    "SKILL.md",
    ".mcp.json",
    ".lsp.json",
)


@dataclass(frozen=True)
class SourceFile:
    """One **file** (never a directory) as the caller found it on disk.

    `path` is relative to the tree root, exactly as the caller's tree walker produced
    it — `plan_archive` normalises it before using it for anything. The plugin-marker
    inference in `plan_archive` depends on the caller listing files only: a root path
    segment is treated as a directory-marker candidate if and only if some listed path
    has a `/` after it, and as a file-marker candidate otherwise. A caller that also
    listed directories as entries would silently break that inference.

    `size` and `is_symlink` MUST be read from `lstat` on this entry itself, never from
    `stat` (i.e. never resolved through a symlink). `is_symlink` is the field this
    module's symlink refusal actually rests on: a caller that resolves links before
    reporting, and passes `is_symlink=False` for what is really a symlink, defeats that
    refusal entirely — silently, with nothing here able to detect it.
    """

    path: str
    size: int
    is_symlink: bool = False
    is_executable: bool = False


@dataclass(frozen=True)
class ArchiveEntry:
    """One planned archive member: where it goes and the mode it is stored with.

    `path` is the *normalised* form of the source path (see `plan_archive`) — not
    necessarily the exact string the caller supplied.
    """

    path: str
    mode: int


def _escape_reason(path: str) -> str | None:
    """Return why `path` is unsafe to extract under the tree root, or None if it's fine.

    `"backslash"` and `"escapes"` are reported separately because they are different
    problems for a reader. A backslash path does not escape the root under POSIX
    semantics — `posixpath` treats `\\` as an ordinary filename character, so the whole
    string normalises to one opaque segment — but a consumer that later extracts the
    archive on a system where `\\` is a path separator would read `..\\evil` as `..`
    then `evil`, and walk out of the root there. Refusing it unconditionally closes that
    gap without needing to know what will extract the archive; calling it an "escape"
    would be actively misleading, since under POSIX it plainly isn't one.
    """
    if "\\" in path:
        return "backslash"
    if posixpath.isabs(path):
        return "escapes"
    normalised = posixpath.normpath(path)
    if normalised == ".." or normalised.startswith("../") or normalised == ".":
        return "escapes"
    return None


def _has_marker(paths: list[str]) -> bool:
    for path in paths:
        head, sep, _ = path.partition("/")
        if sep:
            if head in PLUGIN_MARKER_DIRS:
                return True
        elif path in PLUGIN_MARKER_FILES:
            return True
    return False


def _layout_error(paths: list[str]) -> ArchiveLayoutError:
    found_dirs = sorted({p.partition("/")[0] for p in paths if "/" in p})
    found_files = sorted({p for p in paths if "/" not in p})
    return ArchiveLayoutError(
        "no plugin content at the root — "
        f"root directories found: {', '.join(found_dirs) if found_dirs else 'none'}; "
        f"root files found: {', '.join(found_files) if found_files else 'none'}; "
        f"expected a directory marker among [{', '.join(PLUGIN_MARKER_DIRS)}] "
        f"or a file marker among [{', '.join(PLUGIN_MARKER_FILES)}] (matched case-sensitively)"
    )


def plan_archive(
    files: list[SourceFile], *, max_bytes: int = MAX_ARCHIVE_BYTES
) -> list[ArchiveEntry]:
    """Order and validate the archive members, or raise ArchiveError.

    Every file is checked once, in a single pass:

    1. A negative declared size is a caller bug, not a third-party attack, so it is
       raised on immediately rather than accumulated with the rest.
    2. Symlinks are collected across the whole list and reported together, because a
       third-party tree with one usually has several (`node_modules`, doc symlinks) and
       this function already walks the whole list — accumulating the rest is nearly free.
    3. Paths containing a backslash, and paths that otherwise escape the root, are each
       collected and reported together, backslash paths first.

    Only once every file has cleared all of the above are their *normalised* paths
    checked for collisions — refusing both an identical path listed twice and a
    case-insensitive collision (`SKILL.md` vs `skill.MD`). On the case-insensitive
    filesystems this front door exists to protect (macOS, Windows), a zip holding both
    lets extraction order — not what was reviewed — decide which one lands, and for the
    case-variant kind the reviewed member name still appears in the listing. Refusing is
    the safer default given those target platforms. Normalisation happens first because
    it can itself create a duplicate (`./a.md` and `a.md` normalise to the same member),
    so checking the raw paths for collisions would miss that case.

    Finally the total size is compared against `max_bytes`, and the tree is checked for
    a plugin marker at its root. The returned entries use the normalised path throughout
    — for the escape check, the collision check, the marker check, the sort key, and
    `ArchiveEntry.path` — so a member name like `skills/../skills/a.md`, which does not
    escape the root but is exactly the shape zip-slip detectors flag, is never packaged
    as such: what's stored is the canonical path a human reviewing the plan would expect.
    """
    for file in files:
        if file.size < 0:
            raise ArchiveError(f"file has a negative declared size: {file.path} ({file.size})")

    symlinks = sorted(file.path for file in files if file.is_symlink)
    if symlinks:
        raise ArchiveError(
            "refusing to package symlinks — dereference them to real files or exclude "
            "them from the source tree before packaging: " + ", ".join(symlinks)
        )

    # Keyed by list position, not by path, so two files that happen to share a raw path
    # (itself refused below, once past this point) can never collapse into one report.
    reasons = [_escape_reason(file.path) for file in files]
    backslash_paths = sorted(
        file.path for file, reason in zip(files, reasons, strict=True) if reason == "backslash"
    )
    if backslash_paths:
        raise ArchiveError(
            "refusing to package paths containing a backslash — safe under POSIX "
            "extraction, but would traverse outside the root on an extractor that treats "
            "'\\' as a path separator: " + ", ".join(backslash_paths)
        )
    escaping_paths = sorted(
        file.path for file, reason in zip(files, reasons, strict=True) if reason == "escapes"
    )
    if escaping_paths:
        raise ArchiveError(
            "refusing to package paths that escape the root: " + ", ".join(escaping_paths)
        )

    normalised = [(file, posixpath.normpath(file.path)) for file in files]

    collisions: dict[str, list[str]] = {}
    for _, path in normalised:
        collisions.setdefault(path.casefold(), []).append(path)
    colliding = {key: paths for key, paths in collisions.items() if len(paths) > 1}
    if colliding:
        details = []
        for paths in colliding.values():
            unique = sorted(set(paths))
            if len(unique) == 1:
                details.append(f"{unique[0]} (listed {len(paths)} times)")
            else:
                details.append(f"{' / '.join(unique)} (collide once packaged)")
        raise ArchiveError(
            "refusing to package paths that collide as archive members: "
            + "; ".join(sorted(details))
        )

    total = sum(file.size for file, _ in normalised)
    if total > max_bytes:
        raise ArchiveError(
            f"tree exceeds the archive bound: {total / 2**20:.2f} MiB > "
            f"{max_bytes / 2**20:.2f} MiB — trim or exclude files before packaging"
        )

    paths = [path for _, path in normalised]
    if not _has_marker(paths):
        raise _layout_error(paths)

    return [
        ArchiveEntry(path=path, mode=0o755 if file.is_executable else 0o644)
        for file, path in sorted(normalised, key=lambda pair: pair[1])
    ]
