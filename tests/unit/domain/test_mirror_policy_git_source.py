"""Git sources parse alongside registry sources, and every existing policy still parses."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def test_image_requires_registry_source() -> None:
    text = GIT_POLICY.replace("artifactType: skill", "artifactType: image")
    with pytest.raises(
        PolicyValidationError,
        match="artifactType 'image' requires a registry source, found a GitSource",
    ):
        parse_mirror_policy(text)


def test_helm_chart_requires_registry_source() -> None:
    text = GIT_POLICY.replace("artifactType: skill", "artifactType: helmChart")
    with pytest.raises(
        PolicyValidationError,
        match="artifactType 'helmChart' requires a registry source, found a GitSource",
    ):
        parse_mirror_policy(text)


def test_skill_requires_git_source() -> None:
    text = REGISTRY_POLICY.replace("artifactType: image", "artifactType: skill")
    with pytest.raises(
        PolicyValidationError,
        match="artifactType 'skill' requires a git source, found a RegistrySource",
    ):
        parse_mirror_policy(text)


def test_generic_accepts_registry_source() -> None:
    text = REGISTRY_POLICY.replace("artifactType: image", "artifactType: generic")
    policy = parse_mirror_policy(text)
    assert isinstance(policy.spec.source, RegistrySource)


def test_generic_accepts_git_source() -> None:
    text = GIT_POLICY.replace("artifactType: skill", "artifactType: generic")
    policy = parse_mirror_policy(text)
    assert isinstance(policy.spec.source, GitSource)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/agent-skill.git",
        "ssh://git@github.com/example/agent-skill.git",
        "git@github.com:example/agent-skill.git",
    ],
)
def test_git_source_accepts_allowlisted_url_schemes(url: str) -> None:
    s = GitSource.model_validate({"url": url})
    assert s.url == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///etc/passwd",
        # git's remote-helper syntax executes a shell command at clone time — this is a
        # supply-chain front door, so it must be rejected in the schema, not the adapter.
        "ext::sh -c 'echo pwned'",
        "http://github.com/example/agent-skill.git",
        "git://github.com/example/agent-skill.git",
        # A YAML block scalar appends a trailing newline; `$` (unlike `\Z`) matches
        # right before it, so an unanchored end would let a trailing "\n" slip through.
        "https://github.com/example/agent-skill.git\n",
        "git@github.com:example/agent-skill.git\n",
        # Real git treats a leading `-` in the scp-branch user part as an option, not
        # part of a repository — e.g. `-oProxyCommand=...` or `-c core.sshCommand=...`.
        "-a@github.com:example/agent-skill.git",
        "-c@github.com:core.sshCommand=id",
    ],
)
def test_git_source_rejects_unsafe_url_schemes(url: str) -> None:
    with pytest.raises(ValidationError):
        GitSource.model_validate({"url": url})
