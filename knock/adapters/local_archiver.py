"""`ArchiverPort` over the real local filesystem.

Thin by construction: it delegates to `tree_walker.walk_tree` and
`zip_writer.write_archive`, which own the behaviour and carry their own tests. What lives
here is only the seam — the object a composition root hands to `intake_skill` so that
every one of its dependencies arrives the same way, through its signature.

There is deliberately no second implementation, in tests or anywhere else; see
`ports/archiver.py` for why an in-memory one would defeat the point.
"""

from __future__ import annotations

from pathlib import Path

from knock.adapters.tree_walker import walk_tree
from knock.adapters.zip_writer import write_archive
from knock.domain.packaging import ArchiveEntry, SourceFile


class LocalArchiver:
    def walk(self, root: Path) -> list[SourceFile]:
        return walk_tree(root)

    def write_archive(self, root: Path, entries: list[ArchiveEntry], destination: Path) -> None:
        write_archive(root, entries, destination)
