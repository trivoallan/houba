"""Git sources parse alongside registry sources, and every existing policy still parses."""

from __future__ import annotations

import pytest

from knock.domain.mirror_policy import (
    ArtifactType,
    GitSource,
    RegistrySource,
    parse_mirror_policy,
)
from knock.errors import PolicyValidationError

REGISTRY_POLICY = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
spec:
  artifactType: image
  source:
    registry: docker.io
    repository: library/redis
  imports:
    - name: v7
      tags: {}
      destinations:
        - project: demo
          repository: redis
"""

GIT_POLICY = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: example-skill
spec:
  artifactType: skill
  source:
    url: https://github.com/example/agent-skill.git
    ref: v1.2.0
  imports:
    - name: release
      tags: {}
      destinations:
        - project: skills
          repository: example-skill
"""


def test_registry_policy_still_parses_unchanged() -> None:
    policy = parse_mirror_policy(REGISTRY_POLICY)
    assert isinstance(policy.spec.source, RegistrySource)
    assert policy.spec.source.registry == "docker.io"
    assert policy.spec.artifact_type is ArtifactType.image


def test_git_policy_parses() -> None:
    policy = parse_mirror_policy(GIT_POLICY)
    assert isinstance(policy.spec.source, GitSource)
    assert policy.spec.source.url == "https://github.com/example/agent-skill.git"
    assert policy.spec.source.ref == "v1.2.0"
    assert policy.spec.artifact_type is ArtifactType.skill


def test_git_source_defaults_ref_to_head() -> None:
    text = GIT_POLICY.replace("    ref: v1.2.0\n", "")
    policy = parse_mirror_policy(text)
    assert isinstance(policy.spec.source, GitSource)
    assert policy.spec.source.ref == "HEAD"


def test_mixed_source_is_rejected() -> None:
    text = GIT_POLICY.replace(
        "    url: https://github.com/example/agent-skill.git\n",
        "    url: https://github.com/example/agent-skill.git\n    registry: docker.io\n",
    )
    with pytest.raises(PolicyValidationError):
        parse_mirror_policy(text)


def test_skill_must_not_declare_transform() -> None:
    text = GIT_POLICY.replace(
        "      destinations:\n",
        "      transform:\n        - setTimezone: { zone: Europe/Paris }\n      destinations:\n",
    )
    with pytest.raises(PolicyValidationError, match="must not declare transform"):
        parse_mirror_policy(text)
