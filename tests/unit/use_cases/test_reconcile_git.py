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
    source = FakeSourcePort(revisions={(_URL, _REF): _REV})
    planner = _planner(source, FakeRegistryPort(tags={_DEST: []}))
    planner.plan([policy])
    assert source.resolved == [(_URL, _REF)]
    assert source.fetched == []


def test_plan_emits_the_ref_name_as_a_moving_alias(policy: MirrorPolicy) -> None:
    source = FakeSourcePort(revisions={(_URL, _REF): _REV})
    aliases = _planner(source, FakeRegistryPort(tags={_DEST: []})).plan([policy])
    assert [(a.dest_repo, a.alias, a.target) for a in aliases] == [
        (_DEST, _REF, f"{REVISION_TAG_PREFIX}{_REV}")
    ]


def test_plan_consults_the_destination_tag_list(policy: MirrorPolicy) -> None:
    # Convergence rests on this read and nothing else. Whether it *results* in a skip
    # is asserted against the operations `apply` emits — an `is_up_to_date` accessor on
    # the planner would be production API that exists only for a test.
    source = FakeSourcePort(revisions={(_URL, _REF): _REV})
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
