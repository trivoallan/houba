"""Local packaging port: describe a real tree, and turn a plan for it into an archive file.

Two methods rather than one `package(root, destination)`, so the *decision* stays where
decisions belong: `plan_archive` is pure domain — it is what refuses symlinks, root
escapes, member collisions, the size bound and a missing plugin marker — and the use case
calls it between these two. Folding it into the adapter would bury a domain refusal inside
I/O. This mirrors `reconcile`, which renders a Dockerfile (domain) and then hands the
materialised paths to `ImageBuilderPort`.

**Implementations of this port are deliberately exercised for real in tests, never faked.**
That is the opposite of the usual reason a port exists, and it is not an oversight — it is
the lesson of the task that introduced this port. The two defects that got through the
plan's own passing tests were both invisible to a fake tree: a naive walk archived the
`.git/` directory that `GitAdapter.fetch` leaves in every fetched tree (hook samples, the
index, and `.git/config` with its possibly credentialed remote URL) into an artifact
installed on developer workstations; and the archive was staged inside the very tree it was
packaging. Neither is reachable through an in-memory archiver, because both are properties
of a real filesystem. The port exists so `intake_skill` is injected consistently — it
already receives `SourcePort` and `RegistryPort`, and reaching directly into two adapter
modules would have made half its dependencies invisible in its signature — not so the
filesystem can be stubbed out. If you are about to write an in-memory implementation of
this Protocol for a test, that is the mistake this docstring exists to stop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from knock.domain.packaging import ArchiveEntry, SourceFile


class ArchiverPort(Protocol):
    def walk(self, root: Path) -> list[SourceFile]:
        """Describe every file under `root` for `plan_archive`.

        Symlinks must be reported (`is_symlink=True`), never followed and never silently
        omitted — the planner's symlink refusal rests entirely on that flag. VCS metadata
        must be excluded: it is never skill content, and it is how a fetched git working
        copy leaks its remote URL into a published artifact.
        """
        ...

    def write_archive(self, root: Path, entries: list[ArchiveEntry], destination: Path) -> None:
        """Write the planned `entries`, read from under `root`, to `destination`.

        All-or-nothing: `destination` is either the complete archive or exactly what it
        was before. `destination` must not be under `root` — see `use_cases/intake.py`.
        """
        ...
