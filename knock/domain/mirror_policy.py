"""Pure-domain models for the MirrorPolicy declaration (see spec §3).

YAML is camelCase; Python is snake_case. Unknown fields are rejected so typos in
a policy file fail fast (the schema is the public API).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from knock.domain.deletion_mode import DeletionMode
from knock.errors import PolicyValidationError


class _CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ArtifactType(StrEnum):
    image = "image"
    helm_chart = "helmChart"
    generic = "generic"
    skill = "skill"


class RegistrySource(_CamelModel):
    registry: str = Field(description="Source registry host, e.g. `docker.io`.")
    repository: str = Field(description="Source repository, e.g. `library/redis`.")


# Git's remote-helper syntax (`ext::sh -c ...`) executes an arbitrary shell command at
# clone time, so this is not just a shape check: it is what keeps a hostile policy file
# from turning this front door into an arbitrary-command entry point. Only https/ssh
# URLs and the scp-like `user@host:path` form are accepted; everything else — including
# `ext::`, `file://`, plain `http://`/`git://`, and the empty string — is rejected.
#
# Every user and host part must start with an alphanumeric: a leading `-` makes
# git (or ssh) read it as an option rather than a hostname. Git self-defends here since
# 2.14.1, so this is defense in depth — but the validator must not be the layer that waves
# it through. And the character class is `[^\s\x00]`, not `\S`: `\S` matches a NUL byte,
# which survives all the way to `subprocess`, where Python raises `ValueError("embedded
# null byte")` — outside the KnockError hierarchy, so a traceback instead of an exit code.
_GIT_URL_RE = re.compile(
    r"^(?:https://[A-Za-z0-9][^\s\x00]*"
    r"|ssh://[A-Za-z0-9][^\s\x00]*"
    r"|[A-Za-z0-9][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*:[^\s\x00]+)\Z"
)


def _validate_git_url(value: str) -> str:
    if not _GIT_URL_RE.match(value):
        raise ValueError(
            f"invalid git url {value!r}: expected https://, ssh://, or the scp-like "
            "user@host:path form; git remote helpers (e.g. `ext::`) are not allowed"
        )
    return value


GitUrl = Annotated[str, AfterValidator(_validate_git_url)]


# `ref` and `path` are the other two operator-authored strings that reach `subprocess`,
# and neither had a validator. A ref beginning with `-` is the sharp one: git parses
# options after positionals, so `ref: --upload-pack=<cmd>` makes git execute <cmd>
# (verified against git 2.54.0). The adapter also passes `--` before its positionals;
# these validators are the domain half of that defense, and refuse the policy before any
# adapter runs, naming the field the operator has to fix.
#
# Deliberately narrower than git's `check-ref-format` rather than a port of it: the
# domain layer is pure, so it cannot shell out to git to ask, and a conservative
# allowlist that rejects a valid-but-exotic ref is a clear, fixable error — whereas
# anything this lets through reaches a subprocess at the intake front door.
_GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]*\Z")

# Same class, but a leading `.` is allowed: `.claude/skills/<name>` is an ordinary
# location for a skill. A leading `/` is not — that is an absolute path, not a
# subdirectory of the fetched tree.
_GIT_PATH_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/+-]*\Z")


def _validate_git_ref(value: str) -> str:
    # `..` is a revision range to git and a traversal to everything else; never a ref.
    if not _GIT_REF_RE.match(value) or ".." in value:
        raise ValueError(
            f"invalid git ref {value!r}: expected a branch, tag, or commit sha "
            "([A-Za-z0-9._/+-], starting with an alphanumeric, with no '..')"
        )
    return value


def _validate_git_path(value: str) -> str:
    if not _GIT_PATH_RE.match(value) or ".." in value:
        raise ValueError(
            f"invalid path {value!r}: expected a relative sub-directory of the "
            "repository ([A-Za-z0-9._/+-], not starting with '/' or '-', with no '..')"
        )
    return value


GitRef = Annotated[str, AfterValidator(_validate_git_ref)]
GitPath = Annotated[str, AfterValidator(_validate_git_path)]


class GitSource(_CamelModel):
    url: GitUrl = Field(
        description="Upstream git repository URL, e.g. `https://github.com/o/r.git`."
    )
    ref: GitRef = Field(
        default="HEAD",
        description="Branch, tag, or commit to ingest. Resolved to an immutable commit sha.",
    )
    path: GitPath | None = Field(
        default=None,
        description="Sub-directory holding the artifact; the repository root when omitted.",
    )


# A plain union, not a discriminated one: every member sets `extra="forbid"` and their
# required fields are disjoint, so exactly one member can ever match a given document.
# This keeps the change additive — no discriminator field, no apiVersion bump.
Source = RegistrySource | GitSource


class Destination(_CamelModel):
    registry: str | None = Field(
        default=None,
        description="Logical registry name from the roster; "
        "may be omitted iff exactly one registry is configured.",
    )
    project: str = Field(description="Destination project / namespace.")
    repository: str = Field(description="Destination repository.")


class TagSelection(_CamelModel):
    include_regex: str | None = Field(
        default=None,
        description="Only tags matching this regex are selected (applied before excludes).",
    )
    exclude_regex: list[str] = Field(
        default_factory=list, description="Tags matching any of these regexes are dropped."
    )
    semver_only: bool = Field(
        default=True,
        description="Keep only tags parseable as semver (drops `latest`, date tags, …).",
    )
    names: list[str] = Field(
        default_factory=list, description="Explicit tag names to always include."
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Moving-tag alias templates (e.g. `{major}.{minor}`, `latest`) "
        "re-pointed every run.",
    )


class TransformStep(_CamelModel):
    """One transform step.

    YAML encodes it as a single-key map ``{stepName: params}``; parsed to ``{name, params}``.
    """

    name: str = Field(
        description="Transform step name, e.g. `injectCA` / `rewritePackageSources` / "
        "`setTimezone`.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="Step parameters (shape depends on the step)."
    )

    @model_validator(mode="before")
    @classmethod
    def _from_single_key_map(cls, data: Any) -> Any:
        # Accept the already-split {name, params} form (e.g. when re-validating)...
        if isinstance(data, dict) and set(data.keys()) <= {"name", "params"}:
            return data
        # ...otherwise expect the YAML single-key form {stepName: params}.
        if not isinstance(data, dict) or len(data) != 1:
            raise ValueError("a transform step must be a single-key map {stepName: params}")
        ((name, params),) = data.items()
        return {"name": name, "params": params or {}}


class Archive(_CamelModel):
    keep: int | None = Field(
        default=None, description="Retain the N most-recently-imported tags of each stream."
    )
    older_than_days: int | None = Field(
        default=None,
        description="Of the surplus, only mark tags older than this many days "
        "(both conditions hold).",
    )


class Variant(_CamelModel):
    name: str = Field(description="Variant name.")
    suffix: str = Field(default="", description="Tag suffix appended for this variant, e.g. `-eu`.")
    transform: list[TransformStep] | None = Field(
        default=None, description="Per-variant transform; `null` ⇒ inherit the resolved transform."
    )


# ponytail: shape-only check, not a Backstage catalog lookup. Accepts the three
# Backstage entity-ref forms: name | namespace/name | kind:namespace/name.
# Upgrade path: resolve/validate against a real catalog when one is wired.
_OWNER_RE = re.compile(r"^([A-Za-z0-9]+:)?([A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+$")


def _validate_owner(value: str) -> str:
    if not _OWNER_RE.match(value):
        raise ValueError(f"invalid owner ref {value!r}: expected [kind:][namespace/]name")
    return value


# A Backstage organizational-entity reference, validated by shape only.
Owner = Annotated[str, AfterValidator(_validate_owner)]


class Defaults(_CamelModel):
    destinations: list[Destination] | None = Field(
        default=None, description="Default destinations for every import."
    )
    transform: list[TransformStep] | None = Field(
        default=None, description="Default transform steps for every import."
    )
    archive: Archive | None = Field(
        default=None, description="Default retention policy for every import."
    )
    tags: TagSelection | None = Field(
        default=None, description="Default tag-selection rules for every import."
    )
    platforms: list[str] | None = Field(
        default=None, description="Default platforms for every import."
    )
    owners: list[Owner] | None = Field(
        default=None, description="Default owners (Backstage entity refs) for every import."
    )
    vendor: str | None = Field(
        default=None,
        description="Default vendor for every import, stamped as "
        "`org.opencontainers.image.vendor` (the rebuilding organization).",
    )


class ImportProfile(_CamelModel):
    name: str = Field(
        description="Import name; part of the three-level policy/import/variant identity "
        "in the stamp.",
    )
    tags: TagSelection = Field(description="Tag-selection rules for this import.")
    destinations: list[Destination] | None = Field(
        default=None, description="Destinations (overrides defaults)."
    )
    transform: list[TransformStep] | None = Field(
        default=None, description="Transform steps (overrides defaults)."
    )
    archive: Archive | None = Field(
        default=None, description="Retention policy (overrides defaults)."
    )
    platforms: list[str] | None = Field(default=None, description="Platforms (overrides defaults).")
    variants: list[Variant] | None = Field(
        default=None, description="Variants to fan this import into."
    )
    owners: list[Owner] | None = Field(
        default=None, description="Owners as Backstage entity refs (stamped as `io.knock.owners`)."
    )
    vendor: str | None = Field(
        default=None,
        description="Vendor (overrides defaults), stamped as `org.opencontainers.image.vendor`.",
    )


# Artifact types that only ever come from a container registry (rebuildable, transformable).
_REGISTRY_ONLY: frozenset[ArtifactType] = frozenset({ArtifactType.image, ArtifactType.helm_chart})

# Artifact types that are content, not rebuildable: no transform steps make sense for them.
# `generic` accepts either source kind on purpose (ports/source.py is written generic so
# later artifact classes can also come from git); `skill` is git-only (see
# `_source_matches_artifact_type` below) but shares the no-transform rule with `generic`.
_NON_REBUILDABLE: frozenset[ArtifactType] = frozenset({ArtifactType.generic, ArtifactType.skill})


class Spec(_CamelModel):
    artifact_type: ArtifactType = Field(
        description="Artifact kind: `image` | `helmChart` | `generic` | `skill`."
    )
    source: Source = Field(description="Upstream source: a registry, or a git repository.")
    deletion_mode: DeletionMode | None = Field(
        default=None,
        description="Policy-level deletion mode; `null` ⇒ defer to the destination/global cascade.",
    )
    defaults: Defaults | None = Field(
        default=None, description="Defaults inherited by every import."
    )
    imports: list[ImportProfile] = Field(
        min_length=1, description="One or more import profiles (at least one)."
    )

    @model_validator(mode="after")
    def _source_matches_artifact_type(self) -> Self:
        # Asymmetric on purpose: image/helmChart are registry-only today, skill is
        # git-only, and generic deliberately accepts either (see `_NON_REBUILDABLE`
        # above) so later artifact classes can also be sourced from git.
        kind = self.artifact_type.value
        # Derived from the type, not hard-coded, so this stays correct if a third
        # member ever joins the Source union.
        found = type(self.source).__name__
        if self.artifact_type in _REGISTRY_ONLY and not isinstance(self.source, RegistrySource):
            raise PolicyValidationError(
                f"artifactType '{kind}' requires a registry source, found a {found}"
            )
        if self.artifact_type is ArtifactType.skill and not isinstance(self.source, GitSource):
            raise PolicyValidationError(
                f"artifactType '{kind}' requires a git source, found a {found}"
            )
        return self

    @model_validator(mode="after")
    def _non_rebuildable_has_no_transform(self) -> Self:
        if self.artifact_type not in _NON_REBUILDABLE:
            return self
        kind = self.artifact_type.value
        if self.defaults is not None and self.defaults.transform:
            raise PolicyValidationError(f"artifactType '{kind}' must not declare transform steps")
        for imp in self.imports:
            if imp.transform:
                raise PolicyValidationError(
                    f"artifactType '{kind}' must not declare transform steps (import '{imp.name}')"
                )
        return self


class Metadata(_CamelModel):
    name: str = Field(
        description="Policy name; stamped as `io.knock.policy` and used for collision checks."
    )
    labels: dict[str, str] = Field(
        default_factory=dict, description="Free-form labels (not stamped)."
    )


class MirrorPolicy(_CamelModel):
    api_version: Literal["knock.io/v1alpha1"] = Field(
        description="API version; pinned to `knock.io/v1alpha1`."
    )
    kind: Literal["MirrorPolicy"] = Field(description="Resource kind; always `MirrorPolicy`.")
    metadata: Metadata = Field(description="Policy metadata (name, labels).")
    spec: Spec = Field(description="Policy specification.")


def parse_mirror_policy(text: str) -> MirrorPolicy:
    """Parse and validate a MirrorPolicy YAML document.

    Raises PolicyValidationError on malformed YAML, a non-mapping root, an unknown
    field, a wrong kind/apiVersion, or any schema/semantic violation.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PolicyValidationError(f"invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise PolicyValidationError("the root YAML document must be a mapping")
    try:
        return MirrorPolicy.model_validate(raw)
    except ValidationError as e:
        raise PolicyValidationError(str(e)) from e


def mirror_policy_json_schema() -> dict[str, Any]:
    """The JSON Schema for a MirrorPolicy, keyed by the public (camelCase) field names.

    Published for editor/CI validation of policy files (see CLAUDE.md: JSON Schema
    systematically). Derived from the Pydantic models — never hand-written.

    Note: transform steps are authored in YAML as a single-key map ``{stepName: params}``.
    The published ``TransformStep`` definition is a discriminated ``oneOf`` over the
    registered steps (derived from each step's params model — see
    ``knock.domain.transforms.schema``), so editors/CI validate the authoring YAML form
    and each step's params.
    """
    schema = MirrorPolicy.model_json_schema(by_alias=True)
    # Tighten the open {name, params} TransformStep into a discriminated union derived
    # from the registry, so editors/CI validate per-step params (the authoring YAML form).
    from knock.domain.transforms.schema import transform_steps_schema

    defs = schema.get("$defs", {})
    if "TransformStep" in defs:
        defs["TransformStep"] = transform_steps_schema()
    return schema
