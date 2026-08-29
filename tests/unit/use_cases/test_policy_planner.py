from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knock.config import RegistryConfig
from knock.domain.mirror_policy import MirrorPolicy, parse_mirror_policy
from knock.errors import InternalError
from knock.use_cases.policy_planner import PolicyPlanner
from knock.use_cases.reconcile_registry import RegistryPlanner
from tests.fakes.image_builder import FakeImageBuilder
from tests.fakes.registry import FakeRegistryPort
from tests.fakes.reporter import FakeReporter

_REGISTRY = """
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

_SKILL = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata: { name: example-skill }
spec:
  artifactType: skill
  source: { url: "https://github.com/example/agent-skill.git", ref: v1.2.0 }
  imports:
    - name: release
      tags: {}
      destinations: [{ project: skills, repository: example-skill }]
"""


@pytest.fixture
def registry_policy() -> MirrorPolicy:
    return parse_mirror_policy(_REGISTRY)


@pytest.fixture
def skill_policy() -> MirrorPolicy:
    return parse_mirror_policy(_SKILL)


def test_registry_planner_satisfies_the_protocol() -> None:
    # mypy is the real assertion here: this annotation fails type-checking if
    # RegistryPlanner stops matching the protocol. Kept as a test so the
    # requirement is visible to a reader of the suite, not only to CI.
    _: type[PolicyPlanner] = RegistryPlanner


def test_registry_planner_claims_registry_sources(registry_policy: MirrorPolicy) -> None:
    planner = RegistryPlanner.__new__(RegistryPlanner)  # `handles` reads no state
    assert planner.handles(registry_policy) is True


def test_registry_planner_disclaims_git_sources(skill_policy: MirrorPolicy) -> None:
    # The driver's dispatch rests on this: a planner that claimed everything would
    # silently swallow the git path Task 6 adds.
    planner = RegistryPlanner.__new__(RegistryPlanner)
    assert planner.handles(skill_policy) is False


def _planner(registry: FakeRegistryPort) -> RegistryPlanner:
    return RegistryPlanner(
        registry=registry,
        builder=FakeImageBuilder(),
        roster={"only": RegistryConfig(host="harbor.corp", username="u", password="p")},
        ca_certs={},
        package_mirrors={},
        build_platform="linux/amd64",
        now=datetime(2025, 1, 1, tzinfo=UTC),
        label_prefix="io.knock",
        dry_run_tags=True,
        dry_run_deletions=True,
    )


def test_apply_before_plan_is_loud() -> None:
    # The silent alternative — an empty, *successful* report — is the worst failure
    # mode for a tool whose product is provenance coverage.
    planner = _planner(FakeRegistryPort())
    with pytest.raises(InternalError):
        planner.apply(reporter=FakeReporter(), executor=None)


def test_planning_twice_replaces_rather_than_doubles(registry_policy: MirrorPolicy) -> None:
    planner = _planner(FakeRegistryPort(tags={"docker.io/library/redis": ["7.2.4"]}))
    first = planner.plan([registry_policy])
    planned_once = planner._plans
    assert planned_once is not None and len(planned_once) == 1

    second = planner.plan([registry_policy])
    planned_twice = planner._plans
    assert planned_twice is not None and len(planned_twice) == 1
    assert [len(p) for _, p in planned_twice] == [len(p) for _, p in planned_once]
    assert second == first
