"""Describe a real tree on disk for `knock.domain.packaging.plan_archive`.

An adapter, not a use-case helper: it is the one place a fetched tree is *read*, and
`packaging.py` is pure by design ("There is no tree-walking adapter in this repo yet, so
this rule is written down for whoever builds one" — `SourceFile`'s docstring). It is also
the choke point every source's tree passes through on its way to an archive, which is why
the VCS exclusion below belongs here rather than in the git adapter: a second `SourcePort`
(a tarball, an http fetch of a release zip) gets the same protection for free, and no
future source can reintroduce the leak by handing back a dirty tree.

    tree on disk ──▶ walk_tree ──▶ [SourceFile] ──▶ plan_archive
                         │
                         ├── .git (dir or gitfile) ──▶ excluded, at any depth
                         ├── symlink (file or dir)  ──▶ reported, never followed
                         └── unlistable directory   ──▶ ArchiveSourceReadError

What it deliberately does not do: decide anything. Refusing symlinks, escaping paths,
collisions and the size bound is `plan_archive`'s job, and it is pure precisely so those
refusals are testable without a filesystem. This module only reports what it found.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

from knock.domain.packaging import SourceFile
from knock.errors import ArchiveSourceReadError

# Only `.git`, because git is the only source knock fetches from today. This is not a
# tidiness rule: `GitAdapter.fetch` runs `git init` *in* the workdir and, for a policy
# with no `path`, returns that same workdir as the tree root — so `.git/` is present in
# every fetched tree, and `.git/config` carries the remote URL, which in a real deployment
# can embed a credential. Everything under it would otherwise be packaged into an artifact
# that is pushed to the internal registry and installed on developer workstations.
#
# Matched as an exact name, never as a prefix: `.gitignore` and `.gitattributes` are files
# an upstream author wrote and they belong in the archive. Add `.hg`/`.svn` here the day a
# `SourcePort` can produce them; there is none, and an exclusion nothing can trigger is a
# rule no test can keep honest.
VCS_METADATA_NAMES = frozenset({".git"})


def _refuse(error: OSError) -> NoReturn:
    """`os.walk`'s default is to swallow listing errors, which would package a *subset* of
    the tree under a stamp asserting it is the whole upstream revision."""
    raise ArchiveSourceReadError(f"could not list the source tree at {error.filename}: {error}")


def walk_tree(root: Path) -> list[SourceFile]:
    """Describe every file under `root`, excluding VCS metadata. Symlinks are reported,
    never followed — including symlinked *directories*, which `os.walk` would otherwise
    hand back in `dirnames` and drop from the archive without a word.

    Sizes follow links (`stat`) and the symlink flag does not (`is_symlink`), as
    `SourceFile` requires: the two must be measured differently, and only entries that
    survive `plan_archive` — which refuses symlinks outright — ever have both.
    """
    files: list[SourceFile] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_refuse, followlinks=False):
        directory = Path(dirpath)
        kept: list[str] = []
        for name in sorted(dirnames):
            if name in VCS_METADATA_NAMES:
                continue
            if _is_symlink(directory / name):
                # Reported so `plan_archive` refuses the tree, and pruned so the walk does
                # not descend through it (`followlinks=False` already declines to, but the
                # entry has to be produced from here — nothing else will see it).
                files.append(_describe(directory / name, root))
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if name in VCS_METADATA_NAMES:
                # A worktree or submodule checkout spells `.git` as a *file* holding
                # `gitdir: <absolute path>` — a path on the intake machine, meaningless
                # to any consumer.
                continue
            files.append(_describe(directory / name, root))
    return files


def _is_symlink(path: Path) -> bool:
    """`Path.is_symlink()` is an `lstat` in disguise, and it does *not* swallow every
    `OSError` — a readable-but-not-searchable parent directory (`r--`) raises
    `PermissionError` straight out of it. Bare, that is not a `KnockError`, so `cli/main.py`
    never handles it and the operator gets a traceback and exit 1 ("your input is invalid")
    for what is a filesystem fault."""
    try:
        return path.is_symlink()
    except OSError as e:
        raise ArchiveSourceReadError(f"could not stat a source path: {path}") from e


def _describe(absolute: Path, root: Path) -> SourceFile:
    relative = absolute.relative_to(root).as_posix()
    if _is_symlink(absolute):
        # Size 0, not the target's: this entry exists to be refused by `plan_archive`, and
        # a `stat` through the link would raise on a broken one.
        return SourceFile(path=relative, size=0, is_symlink=True)
    try:
        stat = absolute.stat()
    except OSError as e:
        raise ArchiveSourceReadError(f"could not stat a source path: {absolute}") from e
    return SourceFile(
        path=relative,
        size=stat.st_size,
        is_executable=bool(stat.st_mode & 0o100),
    )
