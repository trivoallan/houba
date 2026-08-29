"""The git-sourced planner: what the plan phase reads, and what it must not touch.

The archiver is the real `LocalArchiver`, never a fake — see `ports/archiver.py`: the
defects it guards are properties of a real filesystem and are unreachable through an
in-memory tree.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from knock.adapters.local_archiver import LocalArchiver
from knock.config import RegistryConfig
from knock.domain.mirror_policy import MirrorPolicy, parse_mirror_policy
from knock.errors import InternalError, SourceError
from knock.use_cases.policy_planner import PolicyPlanner
from knock.use_cases.reconcile_git import REVISION_TAG_PREFIX, GitPlanner
from knock.use_cases.report import Operation, PolicyReport
from tests.fakes.registry import FakeRegistryPort
from tests.fakes.reporter import FakeReporter
from tests.fakes.source import FakeSourcePort

_REV = "a" * 40
_REV_B = "b" * 40  # the revision a moving ref advanced to, and was then reverted from
_URL = "https://github.com/example/agent-skill.git"
_REF = "v1.2.0"
_NOW = datetime(2026, 8, 29, tzinfo=UTC)
_REVISION_KEY = "org.opencontainers.image.revision"

_SKILL = f"""
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: {{ name: example-skill }}
spec:
  artifactType: skill
  source: {{ url: "{_URL}", ref: {_REF} }}
  imports:
    - name: release
      tags: {{}}
      destinations: [{{ project: skills, repository: example-skill }}]
"""

_IMAGE = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: redis }
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/redis }
  imports:
    - name: v7
      tags: { includeRegex: "^7\\\\." }
      destinations: [{ project: lib, repository: redis }]
"""

_ROSTER = {"default": RegistryConfig(host="registry.example")}
_DEST = "registry.example/skills/example-skill"


@pytest.fixture
def policy() -> MirrorPolicy:
    return parse_mirror_policy(_SKILL)


# Seeded explicitly: FakeSourcePort materialises nothing by default, and the apply
# tests drive the real packaging path, which refuses a tree with no plugin marker.
_TREE = {"SKILL.md": "# probe\n"}


def _planner(
    source: FakeSourcePort,
    registry: FakeRegistryPort,
    *,
    dry_run_tags: bool = False,
    work_dir: Path | None = None,
) -> GitPlanner:
    return GitPlanner(
        registry=registry,
        source=source,
        archiver=LocalArchiver(),
        roster=_ROSTER,
        now=_NOW,
        label_prefix="io.knock",
        dry_run_tags=dry_run_tags,
        work_dir=work_dir,
    )


def test_git_planner_satisfies_the_protocol() -> None:
    # mypy is the real assertion here, as in test_policy_planner.py: the annotation
    # fails type-checking if GitPlanner stops matching the protocol.
    _: type[PolicyPlanner] = GitPlanner


def test_git_planner_claims_git_sources_only(policy: MirrorPolicy) -> None:
    planner = GitPlanner.__new__(GitPlanner)  # `handles` reads no state
    assert planner.handles(policy) is True
    assert planner.handles(parse_mirror_policy(_IMAGE)) is False


def test_plan_resolves_the_ref_and_never_fetches(policy: MirrorPolicy) -> None:
    # The convergence claim: a plan phase that had to clone every repository to say
    # "nothing to do" would not be a dry run, which is why `resolve` exists at all.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    planner = _planner(source, FakeRegistryPort(tags={_DEST: []}))
    planner.plan([policy])
    assert source.resolved == [(_URL, _REF)]
    assert source.fetched == []


def test_plan_emits_the_ref_name_as_a_moving_alias(policy: MirrorPolicy) -> None:
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    aliases = _planner(source, FakeRegistryPort(tags={_DEST: []})).plan([policy])
    assert [(a.dest_repo, a.alias, a.target) for a in aliases] == [
        (_DEST, _REF, f"{REVISION_TAG_PREFIX}{_REV}")
    ]


def test_plan_consults_the_destination_tag_list(policy: MirrorPolicy) -> None:
    # Convergence rests on this read and nothing else. Whether it *results* in a skip
    # is asserted against the operations `apply` emits — an `is_up_to_date` accessor on
    # the planner would be production API that exists only for a test.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(tags={_DEST: []})
    _planner(source, registry).plan([policy])
    assert registry.listed_tags == [_DEST]


def test_an_unresolvable_ref_fails_the_plan_phase(policy: MirrorPolicy) -> None:
    # Fail-fast, before any mutation — the image path's contract for a failing
    # `list_tags`, kept for a failing `ls-remote`.
    planner = _planner(FakeSourcePort(revisions={}), FakeRegistryPort(tags={_DEST: []}))
    with pytest.raises(SourceError):
        planner.plan([policy])


def test_apply_before_plan_is_loud() -> None:
    # The silent alternative — an empty, *successful* report — is the worst failure
    # mode for a tool whose product is provenance coverage.
    planner = _planner(FakeSourcePort(), FakeRegistryPort())
    with pytest.raises(InternalError):
        planner.apply(reporter=FakeReporter(), executor=None)


def _kinds(reports: list[PolicyReport]) -> list[str]:
    return [op.kind for r in reports for t in r.targets for v in t.variants for op in v.operations]


def _operations(reports: list[PolicyReport]) -> list[Operation]:
    return [op for r in reports for t in r.targets for v in t.variants for op in v.operations]


def test_apply_imports_and_aliases_a_new_revision(policy: MirrorPolicy, tmp_path: Path) -> None:
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(tags={_DEST: []})
    reporter = FakeReporter()
    planner = _planner(source, registry, work_dir=tmp_path)
    planner.plan([policy])
    reports = planner.apply(reporter=reporter, executor=None)

    assert _kinds(reports) == ["imported", "aliased"]
    assert [op.applied for op in _operations(reports)] == [True, True]
    assert source.fetched == [(_URL, _REF)]  # exactly one fetch, for the one destination
    assert [ref for ref, *_ in registry.artifacts] == [f"{_DEST}:{REVISION_TAG_PREFIX}{_REV}"]
    # The ref name is a moving alias onto the immutable revision tag, never a second push.
    assert registry.copied == [(f"{_DEST}:{REVISION_TAG_PREFIX}{_REV}", f"{_DEST}:{_REF}")]
    assert reports[0].status == "ok"
    assert (reports[0].totals.imported, reports[0].totals.aliased) == (1, 1)
    assert [ev.kind for ev in reporter.operations] == ["imported", "aliased"]


def _converged(revision: str = _REV) -> FakeRegistryPort:
    """A destination already reconciled onto `revision`: the immutable revision tag is
    placed AND the moving alias designates it — which is the whole convergence
    condition, not just its first half."""
    return FakeRegistryPort(
        tags={_DEST: [f"{REVISION_TAG_PREFIX}{revision}", _REF]},
        annotations={f"{_DEST}:{_REF}": {_REVISION_KEY: revision}},
    )


def test_apply_skips_an_already_placed_revision_without_fetching(
    policy: MirrorPolicy, tmp_path: Path
) -> None:
    # The convergence claim, asserted where it matters: a scheduled run over an
    # unchanged upstream must transfer nothing at all — no fetch, and no push, not
    # even a re-point of an alias that is already correct.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = _converged()
    planner = _planner(source, registry, work_dir=tmp_path)
    planner.plan([policy])
    reports = planner.apply(reporter=FakeReporter(), executor=None)

    assert _kinds(reports) == ["skipped"]
    assert source.fetched == []
    assert registry.artifacts == []
    assert registry.copied == []
    assert reports[0].totals.skipped == 1
    assert reports[0].totals.imported == 0


def test_a_reverted_ref_repoints_its_alias_without_refetching(
    policy: MirrorPolicy, tmp_path: Path
) -> None:
    # The three-step scenario: `v1.2.0` was at A, advanced to B, and has now been
    # reverted to A. `sha-A` is still in the destination, so "the revision is placed"
    # answers yes — while the alias still designates B. Whoever installs by ref name
    # gets B while the policy says A, and nothing reports the discrepancy.
    #
    # `sha-B` is still present too: revision tags are immutable and knock never
    # removes them, which is exactly why the first half of the condition is not enough.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(
        tags={_DEST: [f"{REVISION_TAG_PREFIX}{_REV}", f"{REVISION_TAG_PREFIX}{_REV_B}", _REF]},
        annotations={f"{_DEST}:{_REF}": {_REVISION_KEY: _REV_B}},
    )
    planner = _planner(source, registry, work_dir=tmp_path)
    planner.plan([policy])
    reports = planner.apply(reporter=FakeReporter(), executor=None)

    # The alias follows the ref backwards, onto the revision tag already in place…
    assert registry.copied == [(f"{_DEST}:{REVISION_TAG_PREFIX}{_REV}", f"{_DEST}:{_REF}")]
    # …and the artifact, which is already there, is neither re-fetched nor re-pushed.
    assert source.fetched == []
    assert registry.artifacts == []
    assert _kinds(reports) == ["aliased"]
    assert (reports[0].totals.aliased, reports[0].totals.imported) == (1, 0)
    assert reports[0].totals.skipped == 0
    assert reports[0].status == "ok"


def test_a_missing_alias_is_repointed_from_the_free_tag_list(
    policy: MirrorPolicy, tmp_path: Path
) -> None:
    # An interrupted run can leave the revision tag placed and the alias never created.
    # No second read decides this one: the alias's absence from `list_tags` — a read
    # already paid for — is the whole answer.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(tags={_DEST: [f"{REVISION_TAG_PREFIX}{_REV}"]})
    planner = _planner(source, registry, work_dir=tmp_path)
    planner.plan([policy])
    reports = planner.apply(reporter=FakeReporter(), executor=None)

    assert registry.got_annotations == []
    assert _kinds(reports) == ["aliased"]
    assert source.fetched == []
    assert registry.artifacts == []
    assert registry.copied == [(f"{_DEST}:{REVISION_TAG_PREFIX}{_REV}", f"{_DEST}:{_REF}")]


def test_plan_reads_the_alias_only_when_the_revision_is_already_placed(
    policy: MirrorPolicy,
) -> None:
    # The extra read is paid only where it can change the outcome. With nothing placed,
    # the artifact is pushed and the alias moved regardless of what the alias says
    # today, so asking is spend for an answer nobody consults.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(tags={_DEST: []})
    _planner(source, registry).plan([policy])
    assert registry.got_annotations == []

    converged = _converged()
    _planner(source, converged).plan([policy])
    assert converged.got_annotations == [f"{_DEST}:{_REF}"]


def test_dry_run_reports_a_stale_alias_without_repointing_it(
    policy: MirrorPolicy, tmp_path: Path
) -> None:
    # The repoint is a mutation like any other, so `--dry-run` must plan it, not do it —
    # and must not claim an import of an artifact that is already placed.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(
        tags={_DEST: [f"{REVISION_TAG_PREFIX}{_REV}", _REF]},
        annotations={f"{_DEST}:{_REF}": {_REVISION_KEY: _REV_B}},
    )
    planner = _planner(source, registry, dry_run_tags=True, work_dir=tmp_path)
    planner.plan([policy])
    reports = planner.apply(reporter=FakeReporter(), executor=None)

    assert _kinds(reports) == ["aliased"]
    assert [op.applied for op in _operations(reports)] == [False]
    assert source.fetched == []
    assert registry.artifacts == []
    assert registry.copied == []


def test_dry_run_neither_fetches_nor_pushes(policy: MirrorPolicy, tmp_path: Path) -> None:
    # Decision 3's whole purpose: the plan is shown without materialising a tree.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(tags={_DEST: []})
    planner = _planner(source, registry, dry_run_tags=True, work_dir=tmp_path)
    planner.plan([policy])
    reports = planner.apply(reporter=FakeReporter(), executor=None)

    assert _kinds(reports) == ["imported", "aliased"]
    assert [op.applied for op in _operations(reports)] == [False, False]
    assert source.fetched == []
    assert registry.artifacts == []
    assert registry.copied == []


def test_one_failing_policy_does_not_abort_the_batch(policy: MirrorPolicy, tmp_path: Path) -> None:
    # The image path's invariant, kept: a destination that refuses the push fails its
    # own policy and nothing else in the worklist.
    broken = parse_mirror_policy(
        _SKILL.replace("name: example-skill", "name: broken").replace(
            "repository: example-skill", "repository: broken-skill"
        )
    )
    broken_dest = "registry.example/skills/broken-skill"
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(
        tags={_DEST: [], broken_dest: []},
        fail_put={f"{broken_dest}:{REVISION_TAG_PREFIX}{_REV}"},
    )
    reporter = FakeReporter()
    planner = _planner(source, registry, work_dir=tmp_path)
    planner.plan([broken, policy])
    reports = planner.apply(reporter=reporter, executor=None)

    by_name = {r.name: r for r in reports}
    assert by_name["broken"].status == "failed"
    assert by_name["broken"].error is not None
    assert by_name["example-skill"].status == "ok"
    assert [ref for ref, *_ in registry.artifacts] == [f"{_DEST}:{REVISION_TAG_PREFIX}{_REV}"]
    assert [name for name, _ in reporter.failures] == ["broken"]
