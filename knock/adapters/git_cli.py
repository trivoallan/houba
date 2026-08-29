"""subprocess wrapper around git (upstream ingestion)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from knock.errors import InternalError, SourceError, SourcePathError
from knock.ports.source import FetchedSource

# Same budget as the regctl adapter: generous enough for a large clone over a slow link,
# bounded so a wedged or hostile server cannot pin an intake worker forever.
_TIMEOUT_SECONDS = 300


class GitAdapter:
    def __init__(self, binary: str | None = None) -> None:
        # Lazy resolution, matching RegctlAdapter: constructing the container must not
        # require git to be installed.
        self._bin: str | None = binary

    def _resolve(self) -> str:
        if self._bin is not None:
            return self._bin
        resolved = shutil.which("git")
        if not resolved:
            raise SourceError("git binary not found in PATH")
        self._bin = resolved
        return self._bin

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        try:
            done = subprocess.run(  # noqa: S603
                [self._resolve(), *args],
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as e:
            # Not a CalledProcessError, so the handler below would never see it.
            raise SourceError(f"git {' '.join(args)} timed out after {_TIMEOUT_SECONDS}s") from e
        except subprocess.CalledProcessError as e:
            raise SourceError(f"git {' '.join(args)} failed: {e.stderr.strip()}") from e
        except (OSError, ValueError) as e:
            # Neither is a KnockError, so either would escape the CLI's handler as a
            # traceback: OSError when the binary vanished or the cwd is unreadable,
            # ValueError ("embedded null byte") for a NUL anywhere in an argument.
            raise SourceError(f"git {' '.join(args)} could not be executed: {e}") from e
        return done.stdout

    def fetch(
        self, origin: str, ref: str, workdir: Path, *, path: str | None = None
    ) -> FetchedSource:
        """Clone `origin` at `ref` into `workdir` and resolve the ref to a commit sha."""
        self._claim_workdir(workdir)
        self._run(["init", "-q"], cwd=workdir)
        self._run(["remote", "add", "origin", origin], cwd=workdir)
        # `--` matters: `ref` comes from an operator-authored policy and is not schema
        # validated, and git parses options after positionals — without it a ref of
        # `--upload-pack=<cmd>` runs <cmd> (verified against git 2.54.0).
        #
        # `--depth 1` is deliberate (we only ever need one tree), and it is why a `ref`
        # naming a bare commit sha can fail against servers with
        # `uploadpack.allowReachableSHA1InWant` disabled: such servers only serve
        # advertised ref tips. Branch and tag refs are unaffected. Not worked around
        # here — the fallback is a full fetch, which is the cost this flag exists to
        # avoid at the intake front door.
        self._run(["fetch", "-q", "--depth", "1", "--", "origin", ref], cwd=workdir)
        self._run(["checkout", "-q", "FETCH_HEAD"], cwd=workdir)
        revision = self._run(["rev-parse", "HEAD"], cwd=workdir).strip()
        root = workdir if path is None else self._subdir(workdir, path)
        if not root.is_dir():
            raise SourcePathError(f"path not found in {origin}@{ref}: {path}")
        return FetchedSource(root=root, revision=revision, origin=origin)

    @staticmethod
    def _claim_workdir(workdir: Path) -> None:
        """Take ownership of an empty `workdir`, refusing one that already has content.

        The contract is *refuse*, not *clean*: knock never deletes at a caller-supplied
        path, because an over-eager rmtree on a mistyped directory is a far worse failure
        than an error. And `git init` over leftovers would silently fold a previous
        fetch's files into this run's archive — exactly the provenance lie the stamp
        exists to prevent. The composing use case owns a fresh temp dir, so a dirty
        workdir is a knock bug (exit 4), not operator input and not infrastructure.
        """
        try:
            workdir.mkdir(parents=True, exist_ok=True)
            occupied = any(workdir.iterdir())
        except OSError as e:
            raise SourceError(f"unusable workdir {workdir}: {e}") from e
        if occupied:
            raise InternalError(f"workdir is not empty, refusing to fetch into it: {workdir}")

    @staticmethod
    def _subdir(workdir: Path, path: str) -> Path:
        """Resolve a policy-supplied subdirectory, refusing anything outside the tree."""
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SourcePathError(f"path escapes the fetched tree: {path!r}")
        root = (workdir / candidate).resolve()
        # `.resolve()` follows symlinks, so this also catches a hostile repository
        # shipping `packages/inner -> /etc`, which a bare `is_dir()` would confirm.
        if not root.is_relative_to(workdir.resolve()):
            raise SourcePathError(f"path escapes the fetched tree: {path!r}")
        return root
