"""Ingest a skill from an upstream source and place it as a stamped OCI artifact.

    fetch ──▶ check ──▶ stamp ──▶ walk ──▶ plan_archive ──▶ write_archive ──▶ put_artifact
      │         │         │        │            │                                  │
      │         │         │        │            └── refuses symlinks, escapes, size, layout
      │         │         │        └── excludes VCS metadata; never follows a link
      │         │         └── refuses an empty prefix before the tree is packaged
      │         └── refuses a revision the ref moved to since the caller resolved it
      └── resolves the ref to an immutable revision

A separate use case rather than a branch inside `reconcile`: the intake path shares no
digest bookkeeping with the image path, and keeping it here avoids surgery in a 1066-line
file for the first increment.

Two things about the shape of this function are deliberate and easy to "tidy" back into
bugs:

**The archive is staged outside the fetched tree.** `GitAdapter.fetch` runs `git init` in
`workdir` and, for a policy with no `path`, returns that same workdir as the root — so
`workdir / "artifact.zip"` is *inside* the tree being packaged, as is the temp file
`write_archive` creates beside it for its atomic replace. Today the walk happens first so
nothing self-includes, but that is an ordering accident, and a retry or a second walk
would package the archive into itself. The staging directory here is unrelated to
`workdir`, so the question cannot arise. It also has to stay unrelated to `workdir`:
`GitAdapter._claim_workdir` refuses a workdir that is not empty, so leaving anything
behind there would break the next fetch into a reused path.

**The stamp is built before the tree is zipped.** `build_git_stamp_annotations` refuses an
empty `KNOCK_LABEL_PREFIX` (a `ConfigError`, exit 3), and there is no reason to make an
operator pay for packaging an artifact that was never going to be pushed.

**The archiver is injected, not imported.** `walk` and `write_archive` arrive through
`ArchiverPort` like `source` and `registry` do, so every dependency of this function is
visible in its signature rather than half of them being reached for directly. The port's
docstring carries the other half of that decision: its implementations are exercised for
real in tests, never faked, because both defects above are properties of a real filesystem
and neither is reachable through an in-memory tree.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from knock.domain.packaging import plan_archive
from knock.domain.stamp import build_git_stamp_annotations
from knock.errors import SourceRevisionMismatchError
from knock.ports.archiver import ArchiverPort
from knock.ports.registry import RegistryPort
from knock.ports.source import SourcePort

SKILL_ARTIFACT_TYPE = "application/vnd.knock.skill.v1"
SKILL_MEDIA_TYPE = "application/zip"

# The name of the implicit single variant, spelled exactly as `variants.expand_variants`
# spells it for an import that declares none. Not `""`: `stamp._lineage_annotations`
# always writes the `{prefix}.variant` key, so an empty string stamps an artifact
# asserting its variant *is* the empty string — a third state no reader distinguishes
# from the implicit one, and one the image path never produces.
_IMPLICIT_VARIANT = "default"

_ARCHIVE_NAME = "artifact.zip"


@dataclass(frozen=True)
class IntakeRequest:
    origin: str
    ref: str
    path: str | None
    destination_ref: str
    title: str
    policy: str
    import_name: str
    owners: list[str] | None
    vendor: str | None
    workdir: Path
    expected_revision: str | None = None


@dataclass(frozen=True)
class IntakeResult:
    manifest_digest: str
    blob_sha256: str
    revision: str


def intake_skill(
    request: IntakeRequest,
    *,
    source: SourcePort,
    registry: RegistryPort,
    archiver: ArchiverPort,
    prefix: str,
    now: datetime,
) -> IntakeResult:
    """Fetch, package and place one skill. Raises before any push if the tree is unsafe."""
    fetched = source.fetch(request.origin, request.ref, request.workdir, path=request.path)
    _refuse_a_moved_revision(request, fetched.revision)
    annotations = build_git_stamp_annotations(
        prefix=prefix,
        url=fetched.origin,
        revision=fetched.revision,
        title=request.title,
        created=now,
        owners=request.owners,
        vendor=request.vendor,
        artifact_type="skill",
        policy=request.policy,
        import_name=request.import_name,
        variant=_IMPLICIT_VARIANT,
    )
    entries = plan_archive(archiver.walk(fetched.root))
    with tempfile.TemporaryDirectory(prefix="knock-intake-") as staging:
        archive = Path(staging) / _ARCHIVE_NAME
        archiver.write_archive(fetched.root, entries, archive)
        # Streamed, not `read_bytes()`: `put_artifact` takes a path precisely so a bundle
        # up to `MAX_ARCHIVE_BYTES` (100 MiB) is never materialised in this process, and
        # hashing it into memory here would give that back for nothing.
        with archive.open("rb") as blob:
            blob_sha256 = hashlib.file_digest(blob, "sha256").hexdigest()
        manifest_digest = registry.put_artifact(
            request.destination_ref,
            artifact_type=SKILL_ARTIFACT_TYPE,
            blob_path=archive,
            media_type=SKILL_MEDIA_TYPE,
            annotations=annotations,
        )
    return IntakeResult(
        manifest_digest=manifest_digest,
        blob_sha256=blob_sha256,
        revision=fetched.revision,
    )


def _refuse_a_moved_revision(request: IntakeRequest, fetched: str) -> None:
    """Refuse a tree whose revision is not the one the caller pinned this placement to.

    `ref` is a *moving* name, and this function is called with it long after the caller
    resolved it: a push to that ref in between hands back a different tree, which would
    then be placed under a `destination_ref` derived from the earlier revision and
    stamped with the later one. The tag would assert an identity its bytes do not have —
    for a product whose whole claim is that its labels can be trusted, not a race to
    tolerate.

    Checked here rather than in `GitPlanner`, for two reasons. It runs *before* the
    stamp, the packaging and the push, so a mismatch places nothing at all — a planner
    check could only report a corruption already in the registry. And every caller
    inherits it: the reconcile path today, the CLI verb ADR 0048 deferred, the
    end-to-end test.

    Opt-in (`expected_revision=None` skips it) because it can only be a check for a
    caller that resolved the ref itself. A caller taking a ref straight from an operator
    has nothing to compare against and stamps what it fetched, which is honest — it is
    asserting no earlier identity.
    """
    if request.expected_revision is None or request.expected_revision == fetched:
        return
    raise SourceRevisionMismatchError(
        f"upstream ref '{request.ref}' in {request.origin} moved during the run: "
        f"this placement was planned for revision {request.expected_revision}, but the "
        f"fetch returned {fetched}. Nothing was placed; re-run to converge on the "
        f"ref's current revision."
    )
