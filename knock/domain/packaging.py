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
    "path_escapes_root",
    "path_is_canonical",
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

# The client (Claude Code) accepts a plugin whose root — or exactly one level inside a
# single wrapper directory, the shape `git archive` and a GitHub release tarball produce —
# holds `.claude-plugin/` or one of the entries below. Source of truth:
# `docs/superpowers/specs/2026-08-29-external-skill-intake-design.md`, "## Error handling"
# table, the `packaging | no SKILL.md / invalid layout` row, which states both the marker
# list itself and its "optionally inside a single wrapper directory" clause. (Not the
# earlier "Verified — the client contract (2026-08-29)" section of that same spec — that
# section covers zip determinism, blob digests and redirect handling, verified against
# Claude Code 2.1.251, and never mentions plugin markers; citing it for this list would
# overstate what it actually says.) If the client's accepted layout ever changes, this
# tuple goes stale *silently* — a correctly-formed skill would be refused at intake with a
# message that lists these strings and gives no hint that the list itself, not the skill,
# is what's wrong. Re-verify against that spec row (or the client directly) before trusting
# this list after a Claude Code upgrade.
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

    The two measured fields have **different** rules, and conflating them breaks one of
    them:

    - `is_symlink` MUST be determined without following the link (`lstat`, or
      `Path.is_symlink()`). It is the field this module's symlink refusal actually rests
      on: a caller that resolves links before reporting, and passes `is_symlink=False`
      for what is really a symlink, defeats that refusal entirely — silently, with
      nothing here able to detect it.
    - `size` MUST equal the exact byte count a `read_bytes()` of this path would return
      at the moment the tree was walked, which means it follows links (`stat`). A writer
      turning this plan into bytes compares each entry's actual byte count against it to
      catch the tree changing between planning and writing, so it has to be the number
      that check can trust.

    For every entry that survives planning the two coincide, because symlinks are
    refused — and that is exactly why `is_symlink` cannot be inferred from `size`. A
    `stat`-based size for a symlink matches `read_bytes()` on its *target* precisely, so
    a byte-count check alone can never tell a symlink from a regular file of the same
    length. Both the planner and any writer must refuse symlinks on their own evidence.

    `adapters/tree_walker.py` is the walker that has to honour this; any second one must
    honour it too, which is why the rule lives here rather than in that module.
    """

    path: str
    size: int
    is_symlink: bool = False
    is_executable: bool = False


@dataclass(frozen=True)
class ArchiveEntry:
    """One planned archive member: where it goes, the mode it is stored with, and the
    size it was measured at during planning.

    `path` is the *normalised* form of the source path (see `plan_archive`) — not
    necessarily the exact string the caller supplied. `size` lets a writer catch the
    tree changing underneath it between planning and writing.
    """

    path: str
    mode: int
    size: int


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


def path_escapes_root(path: str) -> bool:
    """True if `path` is unsafe to write into an archive underneath the tree root.

    Public, unlike the `_escape_reason` it wraps, because *two* boundaries must enforce
    this: `plan_archive` when it builds the plan, and any writer that turns a plan into
    archive bytes. A writer is separately reachable with hand-built entries, so the
    planner being careful does not make the writer safe. One predicate, two enforcement
    points.
    """
    return _escape_reason(path) is not None


def path_is_canonical(path: str) -> bool:
    """True if `path` is already in the exact form an archive member must carry.

    Deliberately *not* folded into `path_escapes_root`: a non-canonical path does not
    escape the root, and the two rules belong at different boundaries. `plan_archive`
    normalises on the way in, so a caller may legitimately hand it `./SKILL.md` or the
    `my-skill-1.0/` wrapper a `git archive` tarball produces. A *writer*, by contrast,
    receives entries that have already been through that, so a non-canonical arcname
    there means the entry was hand-built and bypassed the planner.

    The reproducibility argument for this archive format rests on canonical, sorted
    entries; two differently-spelled arcnames for one real path silently break it.
    """
    return posixpath.normpath(path) == path


def _root_marker(paths: list[str]) -> bool:
    """True if some path in `paths` carries a plugin marker at position 0."""
    for path in paths:
        head, sep, _ = path.partition("/")
        if sep:
            if head in PLUGIN_MARKER_DIRS:
                return True
        elif path in PLUGIN_MARKER_FILES:
            return True
    return False


def _has_marker(paths: list[str]) -> bool:
    """True if `paths` carries a plugin marker at the root, or one level inside a
    single wrapper directory.

    The wrapper allowance only applies when every path shares the *same* single root
    segment — the shape a `git archive` tarball or a GitHub release zip produces, where
    the whole tree sits under one directory named after the repository and revision.
    Stripping that one shared segment and checking again is exactly one level of
    nesting, never arbitrary depth: `a/b/skills/foo.md` has a single root segment `a`,
    but after stripping it `b/skills/foo.md` still has `skills` one level further in,
    which `_root_marker` does not see as a root-level marker — so it is correctly
    refused, not accepted.
    """
    if _root_marker(paths):
        return True
    roots = {path.partition("/")[0] for path in paths}
    if len(roots) != 1:
        return False
    (wrapper,) = roots
    inner = [path.removeprefix(f"{wrapper}/") for path in paths if path.startswith(f"{wrapper}/")]
    return bool(inner) and _root_marker(inner)


# incongru-voix: illich — seuil ≈ 22 skills/an — le soumissionnaire, hors de tout compte
#
# Ce portillon promet du temps : personne n'audite plus une skill externe à la main. Le
# calcul des deux termes, en ordre de grandeur assumé.
#
#   Temps rendu     relecture manuelle d'une skill avant adoption ≈ 40 min
#                   → N fois 0,67 h/an
#
#   Temps englouti  construction (4 modules, ~750 l. + tests) ≈ 32 h, amortie sur 3 ans   ≈ 11 h/an
#                   entretien de la liste de marqueurs ci-dessus (voir son commentaire :
#                   elle périme *silencieusement* à chaque montée de version du client)   ≈  4 h/an
#                   ────────────────────────────────────────────────────────────────────────────
#                                                                                        ≈ 15 h/an
#
#   Seuil           15 ÷ 0,67 ≈ 22 skills/an, soit ~2 par mois.
#
#   En dessous de ce volume, le portillon coûte plus qu'il ne rend : à 5 skills/an le
#   rapport est de 15 h contre 3,3 h, soit ≈ 4,5. Au-dessus, il paie. Le chiffre à vérifier
#   avant d'aller plus loin n'est donc pas dans ce fichier — c'est le débit réel d'entrées.
#
# Le second seuil, celui où l'outil produit l'inverse de son but, ne dépend pas du volume
# mais de ce message d'erreur. Une liste de marqueurs périmée refuse une skill correcte en
# affichant la liste comme si elle faisait autorité. Le soumissionnaire en conclut que sa
# skill est mal formée, la contourne, et l'installe à la main : le parc croit alors avoir un
# portillon qui ne certifie plus rien. C'est pire que pas de portillon — et le coût de ce
# détour n'apparaît dans aucun compte, puisqu'il est payé par celui qui soumet, pas par
# celui qui opère.
#
# D'où la dernière clause du message : elle ne corrige pas la péremption, elle la rend
# dicible. Un refus qui nomme sa propre faillibilité laisse la prise à l'usager ; un refus
# qui se présente comme un fait la lui retire.
#
# Convivialité, les trois questions : comprendre ? oui, tout est ici et pur. Réparer ? oui
# pour les refus, non pour la liste, dont la justesse vit chez un client externe. S'en
# passer ? oui — et c'est précisément cette sortie qui transforme un faux refus en
# contournement. Deux oui : l'outil sert son usager. Il ne l'emploie pas.
def _layout_error(paths: list[str]) -> ArchiveLayoutError:
    found_dirs = sorted({p.partition("/")[0] for p in paths if "/" in p})
    found_files = sorted({p for p in paths if "/" not in p})
    return ArchiveLayoutError(
        "no plugin content at the root (or one level inside a single wrapper directory) — "
        f"root directories found: {', '.join(found_dirs) if found_dirs else 'none'}; "
        f"root files found: {', '.join(found_files) if found_files else 'none'}; "
        f"expected a directory marker among [{', '.join(PLUGIN_MARKER_DIRS)}] "
        f"or a file marker among [{', '.join(PLUGIN_MARKER_FILES)}] (matched case-sensitively)"
        " — if this layout looks correct, the marker list above may be stale: re-verify it"
        " against the intake spec (or the client) before assuming the artifact is at fault"
    )


def plan_archive(
    files: list[SourceFile], *, max_bytes: int = MAX_ARCHIVE_BYTES
) -> list[ArchiveEntry]:
    """Order and validate the archive members, or raise ArchiveError.

    Every file is checked once, in a single pass:

    1. A negative declared size is a caller bug, not a third-party attack, so it is
       raised on immediately rather than accumulated with the rest. Zero is a valid
       size (an empty `.gitkeep`, `__init__.py`, or `py.typed` is ordinary in a real
       skill tree) — only strictly negative sizes are rejected.
    2. Symlinks are collected across the whole list and reported together, because a
       third-party tree with one usually has several (`node_modules`, doc symlinks) and
       this function already walks the whole list — accumulating the rest is nearly free.
    3. Paths containing a backslash, and paths that otherwise escape the root, are each
       collected and reported together, backslash paths first.

    Only once every file has cleared all of the above are their *normalised* paths
    checked for collisions — refusing both an identical path listed twice and a
    collision that only differs by ASCII case (`SKILL.md` vs `skill.MD`), compared with
    `str.lower()` rather than `str.casefold()`. `casefold()` performs full Unicode
    folding (ß→ss, ligature decomposition, final vs. medial sigma), which is strictly
    broader than what any target filesystem actually conflates — neither NTFS's upcase
    table nor APFS's folding merges `straße.md` and `strasse.md`, so refusing that pair
    would refuse trees no real extraction could ever confuse. `lower()` still catches
    what this check exists for: `SKILL.md` vs `skill.MD`, and the genuinely confusable
    U+212A Kelvin sign vs `K`. On the case-insensitive filesystems this front door
    exists to protect (macOS, Windows), a zip holding a genuine case collision lets
    extraction order — not what was reviewed — decide which file lands, and for the
    case-variant kind the reviewed member name still appears in the listing; refusing
    is the safer default given those target platforms. Normalisation happens first
    because it can itself create a duplicate (`./a.md` and `a.md` normalise to the same
    member), so checking the raw paths for collisions would miss that case.

    Finally the total size is compared against `max_bytes`, and the tree is checked for
    a plugin marker at its root (see `_has_marker` for the one-level wrapper-directory
    allowance). The returned entries use the normalised path throughout — for the escape
    check, the collision check, the marker check, the sort key, and `ArchiveEntry.path`
    — so a member name like `skills/../skills/a.md`, which does not escape the root but
    is exactly the shape zip-slip detectors flag, is never packaged as such: what's
    stored is the canonical path a human reviewing the plan would expect. It would also,
    unnormalised, satisfy the root marker check without packaging any real plugin
    content (`.claude-plugin/../evil.md` has a root segment of `.claude-plugin`) —
    normalising before the marker check closes that gap too.
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
        collisions.setdefault(path.lower(), []).append(path)
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
            f"tree exceeds the archive bound: {total / 2**20:.2f} MiB ({total} bytes) > "
            f"{max_bytes / 2**20:.2f} MiB ({max_bytes} bytes) — trim or exclude files "
            "before packaging"
        )

    paths = [path for _, path in normalised]
    if not _has_marker(paths):
        raise _layout_error(paths)

    return [
        ArchiveEntry(
            path=path,
            mode=0o755 if file.is_executable else 0o644,
            size=file.size,
        )
        for file, path in sorted(normalised, key=lambda pair: pair[1])
    ]
