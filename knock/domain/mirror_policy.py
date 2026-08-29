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


class GitSource(_CamelModel):
    url: str = Field(description="Upstream git repository URL, e.g. `https://github.com/o/r.git`.")
    ref: str = Field(
        default="HEAD",
        description="Branch, tag, or commit to ingest. Resolved to an immutable commit sha.",
    )
    path: str | None = Field(
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
    def _non_rebuildable_has_no_transform(self) -> Self:
        if self.artifact_type not in (ArtifactType.generic, ArtifactType.skill):
            return self
        kind = self.artifact_type.value
        if self.defaults is not None and self.defaults.transform:
            raise PolicyValidationError(f"artifactType '{kind}' must not declare transform steps")
        for imp in self.imports:
            if imp.transform:
                raise PolicyValidationError(
                    f"artifactType '{kind}' must not declare transform steps "
                    f"(import '{imp.name}')"
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
