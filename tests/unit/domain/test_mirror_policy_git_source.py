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
        # ...and the same applies to the *host* part, which the user-part fix left open:
        # defense in depth (git 2.14.1+ blocks a strange hostname itself, and ssh rejects
        # one containing invalid characters) but the validator should not be the layer
        # that waves it through.
        "ssh://-oProxyCommand=id/repo",
        "a@-h:repo",
        "a@-oProxyCommand:repo",
        # `\S` matches a NUL byte, so this reached subprocess, where Python raises
        # ValueError("embedded null byte") — an escape hatch out of the KnockError
        # hierarchy, i.e. a traceback instead of a clean exit code.
        "https://h/r\x00",
        "git@github.com:o/r\x00",
    ],
)
def test_git_source_rejects_unsafe_url_schemes(url: str) -> None:
    with pytest.raises(ValidationError):
        GitSource.model_validate({"url": url})


@pytest.mark.parametrize(
    "ref",
    ["HEAD", "main", "v1.0.0", "release/1.2", "a" * 40, "feature_x", "rc-1+build.5"],
)
def test_git_source_accepts_ordinary_refs(ref: str) -> None:
    assert GitSource.model_validate({"url": "https://h/r.git", "ref": ref}).ref == ref


@pytest.mark.parametrize(
    "ref",
    [
        "",
        # git parses options after positionals, so a ref beginning with `-` is an
        # option: `--upload-pack=<cmd>` makes git execute <cmd>. The adapter passes
        # `--` as well; this is the domain half of that defense.
        "-oProxyCommand=id",
        "--upload-pack=id",
        "-c",
        # A revision range, not a ref — and `..` is the traversal shape besides.
        "main..evil",
        # NUL and whitespace both reach subprocess as-is.
        "main\x00",
        "ma in",
        "main\n",
        # git's own check-ref-format rejects these metacharacters.
        "HEAD@{1}",
        "refs:main",
        "main^",
        "main~1",
        "re*f",
    ],
)
def test_git_source_rejects_unsafe_refs(ref: str) -> None:
    with pytest.raises(ValidationError):
        GitSource.model_validate({"url": "https://h/r.git", "ref": ref})


@pytest.mark.parametrize(
    "path",
    [".claude/skills/probe", "packages/inner", "skills", "a_b/c-d.e"],
)
def test_git_source_accepts_ordinary_paths(path: str) -> None:
    assert GitSource.model_validate({"url": "https://h/r.git", "path": path}).path == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        # The adapter refuses these too; this stops the policy at the door instead.
        "../etc",
        "packages/../../etc",
        "/etc",
        "-x",
        "a\x00",
        "a b",
    ],
)
def test_git_source_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        GitSource.model_validate({"url": "https://h/r.git", "path": path})


def test_an_unsafe_ref_in_a_policy_is_a_policy_validation_error_exit_1() -> None:
    # End-to-end through the real entry point: the operator gets exit 1 and a message
    # naming the field, not a traceback and not an adapter-level failure.
    from knock.errors import exit_code_for

    text = GIT_POLICY.replace("ref: v1.2.0", "ref: --upload-pack=id")
    assert "--upload-pack" in text, "GIT_POLICY no longer pins `ref: v1.2.0`"
    with pytest.raises(PolicyValidationError) as excinfo:
        parse_mirror_policy(text)
    assert "ref" in str(excinfo.value)
    assert exit_code_for(excinfo.value) == 1


_SKILL_WITH_ARCHIVE = """
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
      archive:
        keep: 3
      destinations:
        - project: skills
          repository: example-skill
"""

_SKILL_WITH_DEFAULTS_ARCHIVE = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: example-skill
spec:
  artifactType: skill
  source:
    url: https://github.com/example/agent-skill.git
    ref: v1.2.0
  defaults:
    archive:
      keep: 3
  imports:
    - name: release
      tags: {}
      destinations:
        - project: skills
          repository: example-skill
"""

_SKILL_WITH_DELETION_MODE = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: example-skill
spec:
  artifactType: skill
  deletionMode: purge
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


def test_skill_import_may_not_declare_archive() -> None:
    # Refused, not ignored: skills are never deleted (spec decision 4), so an author
    # who writes `archive` believes their policy prunes when it never will.
    with pytest.raises(PolicyValidationError, match="must not declare a retention policy"):
        parse_mirror_policy(_SKILL_WITH_ARCHIVE)


def test_skill_defaults_may_not_declare_archive() -> None:
    with pytest.raises(PolicyValidationError, match="must not declare a retention policy"):
        parse_mirror_policy(_SKILL_WITH_DEFAULTS_ARCHIVE)


def test_skill_may_not_declare_deletion_mode() -> None:
    with pytest.raises(PolicyValidationError, match="must not declare a deletion mode"):
        parse_mirror_policy(_SKILL_WITH_DELETION_MODE)


_IMAGE_WITH_ARCHIVE_AND_DELETION_MODE = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
spec:
  artifactType: image
  deletionMode: purge
  source:
    registry: docker.io
    repository: library/redis
  defaults:
    archive:
      keep: 5
  imports:
    - name: v7
      tags: {}
      archive:
        keep: 3
      destinations:
        - project: demo
          repository: redis
"""


def test_image_policy_may_still_declare_archive_and_deletion_mode() -> None:
    # The guard is skill-only. This is the test that actually holds it to that:
    # it declares deletionMode, defaults.archive AND a per-import archive — the three
    # fields the validator refuses — and requires them all to parse for an image.
    # The previous version used a fixture declaring none of them, so it passed even
    # with the validator deleted.
    policy = parse_mirror_policy(_IMAGE_WITH_ARCHIVE_AND_DELETION_MODE)
    assert policy.spec.deletion_mode is not None
    assert policy.spec.defaults is not None and policy.spec.defaults.archive is not None
    assert policy.spec.imports[0].archive is not None
