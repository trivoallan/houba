"""In-memory SourcePort. Journals calls so a use-case test can assert what was asked."""

from __future__ import annotations

from pathlib import Path

from knock.errors import SourceError, SourcePathError
from knock.ports.source import FetchedSource


class FakeSourcePort:
    def __init__(
        self,
        revisions: dict[tuple[str, str], str] | None = None,
        tree: dict[str, str] | None = None,
    ) -> None:
        # Seed data is private and journals are public, matching every other fake here.
        # `resolved` and `fetched` are set unconditionally rather than accepted as
        # arguments: they are the evidence a planner test uses to claim "resolved but
        # never fetched", and a journal a caller can pre-populate is not evidence.
        self._revisions = revisions or {}  # (origin, ref) -> revision; absent ⇒ unknown ref
        # Files `fetch` materialises, relative to the repository root. Empty by
        # default, like every other seed field here: a generic SourcePort fake should
        # not carry skill-domain content, and an unseeded tree that silently produced a
        # file would mask a bug in code asserting nothing extra was materialised.
        # A test driving the real packaging path must seed a plugin marker itself —
        # `{"SKILL.md": "..."}` is the cheapest (see PLUGIN_MARKER_FILES in
        # knock/domain/packaging.py) — or `plan_archive` raises `ArchiveLayoutError`.
        self._tree = tree or {}
        self.resolved: list[tuple[str, str]] = []
        self.fetched: list[tuple[str, str]] = []

    def _revision(self, origin: str, ref: str) -> str:
        try:
            return self._revisions[(origin, ref)]
        except KeyError:
            raise SourceError(f"ref '{ref}' not found in {origin}") from None

    def resolve(self, origin: str, ref: str) -> str:
        self.resolved.append((origin, ref))
        return self._revision(origin, ref)

    def fetch(
        self, origin: str, ref: str, workdir: Path, *, path: str | None = None
    ) -> FetchedSource:
        # `_revision`, not `resolve`: the two journals must stay independent, so a
        # planner test can assert "resolved but never fetched" and mean it.
        revision = self._revision(origin, ref)
        self.fetched.append((origin, ref))
        # Materialise the whole tree at `workdir` first, then re-root into it — the
        # order the real adapter uses (clone, then `workdir / path`). Writing under an
        # already-re-rooted path would nest the subdirectory inside itself.
        workdir.mkdir(parents=True, exist_ok=True)
        for name, content in self._tree.items():
            target = workdir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        root = workdir if path is None else self._subdir(workdir, path, origin, ref)
        return FetchedSource(root=root, revision=revision, origin=origin)

    def _subdir(self, workdir: Path, path: str, origin: str, ref: str) -> Path:
        """Refuse what `GitAdapter._subdir` refuses, for the same reasons.

        A fake that accepts every `path` lets a test for "the policy names a
        subdirectory that is not there" pass while the real adapter exits 1 — the
        failure mode is operator-facing, so the fake has to be able to produce it.
        Existence is checked against the seeded tree rather than the filesystem,
        because nothing has been written yet at this point.
        """
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SourcePathError(f"path escapes the fetched tree: {path!r}")
        prefix = f"{candidate.as_posix()}/"
        if not any(name.startswith(prefix) for name in self._tree):
            raise SourcePathError(f"path not found in {origin}@{ref}: {path}")
        return workdir / candidate
