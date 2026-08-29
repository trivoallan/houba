"""The intake composition: fetch, stamp, zip, push.

The fakes here are shaped after the *real* adapters, not after what is convenient:
`FakeGitSource` materialises the tree inside the caller's `workdir`, leaves a `.git`
directory behind, and returns `root == workdir` — exactly what `GitAdapter.fetch` does.
A fake that hands back some unrelated pristine directory cannot see either of the two
things this suite exists to pin: that repository internals never reach the archive, and
that the archive is never staged inside the tree it is packaging.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from knock.adapters.local_archiver import LocalArchiver
from knock.errors import ArchiveError, ConfigError, exit_code_for
from knock.ports.source import FetchedSource
from knock.use_cases.intake import IntakeRequest, intake_skill
from tests.fakes.registry import FakeRegistryPort

CREATED = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
REVISION = "c0ffee" + "0" * 34
ORIGIN = "https://github.com/example/agent-skill.git"

# What `git init` + `git remote add` leave in the workdir. The remote URL is the reason
# this matters beyond tidiness: in a real deployment it can carry a credential, and an
# intake artifact is pushed to the internal registry and installed on workstations.
_GIT_INTERNALS = {
    ".git/HEAD": "ref: refs/heads/main\n",
    ".git/config": '[remote "origin"]\n\turl = https://x-token:s3cr3t@example.com/r.git\n',
    ".git/hooks/pre-commit.sample": "#!/bin/sh\nexit 0\n",
    ".git/index": "DIRC\x00\x00\x00\x02",
}


class FakeGitSource:
    """A source that behaves like `GitAdapter`: it writes into `workdir` and returns it."""

    def __init__(self, files: dict[str, str], *, revision: str = REVISION) -> None:
        self._files = files
        self._revision = revision

    def fetch(
        self, origin: str, ref: str, workdir: Path, *, path: str | None = None
    ) -> FetchedSource:
        for relative, content in {**_GIT_INTERNALS, **self._files}.items():
            target = workdir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        root = workdir if path is None else workdir / path
        return FetchedSource(root=root, revision=self._revision, origin=origin)


class RecordingRegistry(FakeRegistryPort):
    """The maintained shared fake, plus the blob's bytes captured at push time.

    Subclassed rather than reimplemented so the intake is tested against the same
    preconditions `RegctlAdapter` enforces (a real regular file; no empty or `=`-bearing
    annotation key) — a private stub would be laxer than the adapter it stands for and
    would pass here while failing in production. The bytes have to be read *during* the
    push: the intake stages its archive in a temp directory that is gone by the time a
    test body runs, so a recorded `Path` would be unreadable.
    """

    def __init__(self) -> None:
        super().__init__()
        self.blobs: list[bytes] = []

    def put_artifact(
        self,
        image_ref: str,
        *,
        artifact_type: str,
        blob_path: Path,
        media_type: str,
        annotations: dict[str, str],
    ) -> str:
        self.blobs.append(blob_path.read_bytes())
        return super().put_artifact(
            image_ref,
            artifact_type=artifact_type,
            blob_path=blob_path,
            media_type=media_type,
            annotations=annotations,
        )

    def members(self) -> list[str]:
        with zipfile.ZipFile(io.BytesIO(self.blobs[-1])) as zf:
            return sorted(zf.namelist())


def request_for(workdir: Path, *, path: str | None = None) -> IntakeRequest:
    return IntakeRequest(
        origin=ORIGIN,
        ref="v1.0.0",
        path=path,
        destination_ref="registry.example/skills/probe:1.0.0",
        title="probe",
        policy="example-skill",
        import_name="release",
        owners=["group:default/platform"],
        vendor=None,
        workdir=workdir,
    )


def _skill_files(prefix: str = "") -> dict[str, str]:
    return {
        f"{prefix}.claude-plugin/plugin.json": '{"name":"probe"}',
        f"{prefix}SKILL.md": "# probe\n",
    }


def test_pushes_a_stamped_artifact(tmp_path: Path) -> None:
    registry = RecordingRegistry()
    result = intake_skill(
        request_for(tmp_path / "work"),
        source=FakeGitSource(_skill_files()),
        registry=registry,
        archiver=LocalArchiver(),
        prefix="io.knock",
        now=CREATED,
    )
    ref, artifact_type, _blob, media_type, annotations = registry.artifacts[0]
    assert ref == "registry.example/skills/probe:1.0.0"
    assert artifact_type == "application/vnd.knock.skill.v1"
    assert media_type == "application/zip"
    # Whole-contract equality, not a key-set check: what this pins is the *wiring* — the
    # title from the request rather than the origin, the revision and source from the
    # fetch rather than the request's ref, and `variant="default"`. That last one is a
    # decision, not a default: `_lineage_annotations` always writes the variant key, so
    # passing `""` would stamp `io.knock.variant: ""` — an artifact asserting its variant
    # is the empty string. `expand_variants` names the implicit single variant `default`,
    # so the skill path spells it the way the image path already does.
    assert annotations == {
        "org.opencontainers.image.title": "probe",
        "org.opencontainers.image.source": ORIGIN,
        "org.opencontainers.image.revision": REVISION,
        "org.opencontainers.image.created": "2026-08-29T12:00:00+00:00",
        "io.knock.artifact.type": "skill",
        "io.knock.policy": "example-skill",
        "io.knock.import": "release",
        "io.knock.variant": "default",
        "io.knock.owners": "group:default/platform",
    }
    assert result.revision == REVISION
    assert len(result.blob_sha256) == 64
    assert result.manifest_digest.startswith("sha256:")


def test_the_blob_digest_is_stable_across_runs(tmp_path: Path) -> None:
    # Two *different* workdirs, because GitAdapter._claim_workdir refuses a non-empty
    # one: a second intake always gets a fresh directory, and the blob digest must not
    # depend on which directory that was.
    first = intake_skill(
        request_for(tmp_path / "a"),
        source=FakeGitSource(_skill_files()),
        registry=RecordingRegistry(),
        archiver=LocalArchiver(),
        prefix="io.knock",
        now=CREATED,
    )
    second = intake_skill(
        request_for(tmp_path / "b"),
        source=FakeGitSource(_skill_files()),
        registry=RecordingRegistry(),
        archiver=LocalArchiver(),
        prefix="io.knock",
        now=CREATED,
    )
    assert first.blob_sha256 == second.blob_sha256


def test_git_metadata_is_never_published(tmp_path: Path) -> None:
    # The defect a fake source returning a pristine directory cannot see. A naive walk
    # archives 25 files of `.git/` — the remote URL, the executable hook samples, the
    # index — into an artifact that is pushed to the internal registry and installed on
    # developer workstations.
    registry = RecordingRegistry()
    intake_skill(
        request_for(tmp_path / "work"),
        source=FakeGitSource(_skill_files()),
        registry=registry,
        archiver=LocalArchiver(),
        prefix="io.knock",
        now=CREATED,
    )
    assert registry.members() == [".claude-plugin/plugin.json", "SKILL.md"]


def test_the_archive_is_never_staged_inside_the_fetched_tree(tmp_path: Path) -> None:
    # `GitAdapter.fetch` returns `root == workdir`, so an archive written at
    # `workdir / "artifact.zip"` — and the temp file `write_archive` creates beside it
    # for its atomic replace — land inside the tree being packaged.
    workdir = tmp_path / "work"
    intake_skill(
        request_for(workdir),
        source=FakeGitSource(_skill_files()),
        registry=RecordingRegistry(),
        archiver=LocalArchiver(),
        prefix="io.knock",
        now=CREATED,
    )
    left_behind = sorted(
        path.relative_to(workdir).as_posix()
        for path in workdir.rglob("*")
        if path.is_file() and not path.relative_to(workdir).as_posix().startswith(".git/")
    )
    assert left_behind == [".claude-plugin/plugin.json", "SKILL.md"]


def test_a_subdirectory_source_is_packaged_from_that_subdirectory(tmp_path: Path) -> None:
    registry = RecordingRegistry()
    intake_skill(
        request_for(tmp_path / "work", path="packages/probe"),
        source=FakeGitSource(_skill_files("packages/probe/")),
        registry=registry,
        archiver=LocalArchiver(),
        prefix="io.knock",
        now=CREATED,
    )
    assert registry.members() == [".claude-plugin/plugin.json", "SKILL.md"]


def test_an_unsafe_tree_is_never_pushed(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = FakeGitSource(_skill_files())
    registry = RecordingRegistry()

    class WithSymlink(FakeGitSource):
        def fetch(
            self, origin: str, ref: str, workdir: Path, *, path: str | None = None
        ) -> FetchedSource:
            fetched = source.fetch(origin, ref, workdir, path=path)
            (fetched.root / "evil").symlink_to("/etc/passwd")
            return fetched

    with pytest.raises(ArchiveError, match="symlink"):
        intake_skill(
            request_for(workdir),
            source=WithSymlink({}),
            registry=registry,
            archiver=LocalArchiver(),
            prefix="io.knock",
            now=CREATED,
        )
    assert registry.artifacts == []


def test_an_empty_prefix_refuses_before_anything_is_pushed(tmp_path: Path) -> None:
    # `build_git_stamp_annotations` refuses an empty prefix: a base-less stamp under one
    # carries only standard OCI keys and could never read back as covered. The stamp is
    # built before the tree is zipped, so the operator is not billed for the packaging of
    # an artifact that was never going to be pushed.
    registry = RecordingRegistry()
    with pytest.raises(ConfigError, match="KNOCK_LABEL_PREFIX") as exc_info:
        intake_skill(
            request_for(tmp_path / "work"),
            source=FakeGitSource(_skill_files()),
            registry=registry,
            archiver=LocalArchiver(),
            prefix="",
            now=CREATED,
        )
    assert exit_code_for(exc_info.value) == 3
    assert registry.artifacts == []
