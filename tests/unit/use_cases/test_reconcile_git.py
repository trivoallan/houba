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
_URL = "https://github.com/example/agent-skill.git"
_REF = "v1.2.0"
_NOW = datetime(2026, 8, 29, tzinfo=UTC)

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


def test_apply_skips_an_already_placed_revision_without_fetching(
    policy: MirrorPolicy, tmp_path: Path
) -> None:
    # The convergence claim, asserted where it matters: a scheduled run over an
    # unchanged upstream must transfer nothing at all.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV}, tree=_TREE)
    registry = FakeRegistryPort(tags={_DEST: [f"{REVISION_TAG_PREFIX}{_REV}"]})
    planner = _planner(source, registry, work_dir=tmp_path)
    planner.plan([policy])
    reports = planner.apply(reporter=FakeReporter(), executor=None)

    assert _kinds(reports) == ["skipped"]
    assert source.fetched == []
    assert registry.artifacts == []
    assert registry.copied == []
    assert reports[0].totals.skipped == 1
    assert reports[0].totals.imported == 0


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
