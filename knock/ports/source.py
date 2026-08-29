"""Upstream ingestion port.

Generic on purpose: git is the first non-registry source, not the only one it should ever
be able to carry. Writing it git-specific would make the second artifact class a refactor
of a public contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class FetchedSource:
    """A materialised upstream tree plus the immutable identity it was fetched at."""

    root: Path
    revision: str  # immutable upstream identity (a commit sha for git)
    origin: str  # where it came from, stamped as org.opencontainers.image.source


class SourcePort(Protocol):
    def resolve(self, origin: str, ref: str) -> str:
        """Resolve `ref` to its immutable revision **without materialising a tree**.

        Separate from `fetch` so the reconcile plan phase can decide whether anything
        needs importing at all: a `--dry-run` that had to clone every repository to
        say "nothing to do" would not be a dry run. Must agree with `fetch`'s
        `FetchedSource.revision` for the same inputs, or a planner would skip on one
        value and stamp another.
        """
        ...

    def fetch(
        self, origin: str, ref: str, workdir: Path, *, path: str | None = None
    ) -> FetchedSource: ...
