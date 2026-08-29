from __future__ import annotations

import pytest
from pydantic import ValidationError

from knock.domain.deletion_mode import DeletionMode
from knock.domain.mirror_policy import (
    Archive,
    ArtifactType,
    Defaults,
    Destination,
    ImportProfile,
    MirrorPolicy,
    RegistrySource,
    Spec,
    TagSelection,
    TransformStep,
    Variant,
    mirror_policy_json_schema,
    parse_mirror_policy,
)
from knock.errors import PolicyValidationError


def test_source_parses() -> None:
    s = RegistrySource.model_validate({"registry": "docker.io", "repository": "library/redis"})
    assert s.registry == "docker.io"
    assert s.repository == "library/redis"


def test_destination_registry_optional() -> None:
    d = Destination.model_validate({"project": "lib", "repository": "redis"})
    assert d.registry is None
    assert d.project == "lib"


def test_destination_with_named_registry() -> None:
    d = Destination.model_validate(
        {"registry": "harbor-eu", "project": "lib", "repository": "redis"}
    )
    assert d.registry == "harbor-eu"


def test_artifact_type_values() -> None:
    assert ArtifactType("image") is ArtifactType.image
    assert ArtifactType("helmChart") is ArtifactType.helm_chart
    assert ArtifactType("generic") is ArtifactType.generic


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RegistrySource.model_validate({"registry": "docker.io", "repository": "r", "typo": 1})


def test_tag_selection_defaults() -> None:
    t = TagSelection.model_validate({})
    assert t.include_regex is None
    assert t.exclude_regex == []
    assert t.semver_only is True
    assert t.names == []
    assert t.aliases == []


def test_tag_selection_camel_case_input() -> None:
    t = TagSelection.model_validate(
        {
            "includeRegex": "^7\\.",
            "excludeRegex": ["-rc"],
            "semverOnly": False,
            "names": ["7.2.1-special"],
            "aliases": ["{major}.{minor}", "latest"],
        }
    )
    assert t.include_regex == "^7\\."
    assert t.exclude_regex == ["-rc"]
    assert t.semver_only is False
    assert t.names == ["7.2.1-special"]
    assert t.aliases == ["{major}.{minor}", "latest"]


def test_transform_step_from_single_key_map() -> None:
    step = TransformStep.model_validate({"injectCA": {"certs": ["corp-root-ca"]}})
    assert step.name == "injectCA"
    assert step.params == {"certs": ["corp-root-ca"]}


def test_transform_step_with_empty_params() -> None:
    step = TransformStep.model_validate({"enableFips": {}})
    assert step.name == "enableFips"
    assert step.params == {}


def test_transform_step_null_params_becomes_empty() -> None:
    step = TransformStep.model_validate({"enableFips": None})
    assert step.params == {}


def test_transform_step_rejects_multi_key() -> None:
    with pytest.raises(ValueError):
        TransformStep.model_validate({"injectCA": {}, "setTimezone": {}})


def test_transform_step_rejects_empty() -> None:
    with pytest.raises(ValueError):
        TransformStep.model_validate({})


def test_archive_defaults() -> None:
    # Fields are optional (None); constant defaults live in domain.retention.
    a = Archive.model_validate({})
    assert a.keep is None
    assert a.older_than_days is None


def test_archive_camel_input() -> None:
    a = Archive.model_validate({"keep": 5, "olderThanDays": 90})
    assert a.keep == 5
    assert a.older_than_days == 90


def test_variant_minimal() -> None:
    v = Variant.model_validate({"name": "standard", "suffix": ""})
    assert v.name == "standard"
    assert v.suffix == ""
    assert v.transform is None


def test_variant_with_transform() -> None:
    v = Variant.model_validate(
        {"name": "fips", "suffix": "-fips", "transform": [{"enableFips": {}}]}
    )
    assert v.suffix == "-fips"
    assert v.transform is not None
    assert v.transform[0].name == "enableFips"


def test_defaults_all_optional() -> None:
    d = Defaults.model_validate({})
    assert d.destinations is None
    assert d.transform is None
    assert d.archive is None
    assert d.tags is None
    assert d.platforms is None


def test_defaults_populated() -> None:
    d = Defaults.model_validate(
        {
            "platforms": ["linux/amd64", "linux/arm64"],
            "destinations": [{"registry": "harbor-eu", "project": "lib", "repository": "redis"}],
            "transform": [{"injectCA": {"certs": ["corp-root-ca"]}}],
            "archive": {"keep": 2, "olderThanDays": 30},
            "tags": {"semverOnly": True, "excludeRegex": ["-rc"]},
        }
    )
    assert d.platforms == ["linux/amd64", "linux/arm64"]
    assert d.destinations[0].registry == "harbor-eu"
    assert d.transform[0].name == "injectCA"
    assert d.tags.exclude_regex == ["-rc"]


def test_import_profile_minimal() -> None:
    i = ImportProfile.model_validate({"name": "v7", "tags": {"includeRegex": "^7\\."}})
    assert i.name == "v7"
    assert i.tags.include_regex == "^7\\."
    assert i.destinations is None  # inherited from defaults at merge time
    assert i.variants is None


def test_import_profile_full() -> None:
    i = ImportProfile.model_validate(
        {
            "name": "v7",
            "tags": {"includeRegex": "^7\\.", "aliases": ["{major}.{minor}"]},
            "destinations": [{"registry": "harbor-eu", "project": "lib", "repository": "redis"}],
            "transform": [{"setTimezone": {"zone": "Europe/Paris"}}],
            "archive": {"keep": 3},
            "platforms": ["linux/amd64"],
            "variants": [{"name": "standard", "suffix": ""}],
        }
    )
    assert i.platforms == ["linux/amd64"]
    assert i.variants[0].name == "standard"
    assert i.transform[0].name == "setTimezone"


def test_spec_minimal() -> None:
    spec = Spec.model_validate(
        {
            "artifactType": "image",
            "source": {"registry": "docker.io", "repository": "library/redis"},
            "imports": [{"name": "v7", "tags": {"includeRegex": "^7\\."}}],
        }
    )
    assert spec.artifact_type is ArtifactType.image
    assert spec.source.repository == "library/redis"
    assert len(spec.imports) == 1


def test_spec_artifact_type_required() -> None:
    with pytest.raises(Exception):  # noqa: B017
        Spec.model_validate(
            {
                "source": {"registry": "docker.io", "repository": "r"},
                "imports": [{"name": "v", "tags": {}}],
            }
        )


def test_spec_requires_at_least_one_import() -> None:
    with pytest.raises(Exception):  # noqa: B017
        Spec.model_validate(
            {
                "artifactType": "image",
                "source": {"registry": "docker.io", "repository": "r"},
                "imports": [],
            }
        )


def test_spec_generic_without_transform_is_valid() -> None:
    spec = Spec.model_validate(
        {
            "artifactType": "generic",
            "source": {"registry": "docker.io", "repository": "r"},
            "imports": [{"name": "v", "tags": {}}],
        }
    )
    assert spec.artifact_type is ArtifactType.generic


def test_spec_generic_forbids_transform_in_defaults() -> None:
    with pytest.raises(PolicyValidationError, match="generic"):
        Spec.model_validate(
            {
                "artifactType": "generic",
                "source": {"registry": "docker.io", "repository": "r"},
                "defaults": {"transform": [{"injectCA": {}}]},
                "imports": [{"name": "v", "tags": {}}],
            }
        )


def test_spec_generic_forbids_transform_in_import() -> None:
    with pytest.raises(PolicyValidationError, match="generic"):
        Spec.model_validate(
            {
                "artifactType": "generic",
                "source": {"registry": "docker.io", "repository": "r"},
                "imports": [{"name": "v", "tags": {}, "transform": [{"injectCA": {}}]}],
            }
        )


VALID_YAML = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
  labels:
    team: platform-data
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/redis }
  imports:
    - name: v7
      tags: { includeRegex: "^7\\\\." }
"""


def test_parse_valid_policy() -> None:
    policy = parse_mirror_policy(VALID_YAML)
    assert isinstance(policy, MirrorPolicy)
    assert policy.api_version == "knock.io/v1alpha1"
    assert policy.kind == "MirrorPolicy"
    assert policy.metadata.name == "redis"
    assert policy.metadata.labels == {"team": "platform-data"}
    assert policy.spec.imports[0].name == "v7"


def test_parse_rejects_wrong_kind() -> None:
    with pytest.raises(PolicyValidationError):
        parse_mirror_policy(
            "apiVersion: knock.io/v1alpha1\nkind: Wrong\nmetadata: {name: x}\n"
            "spec: {artifactType: image, source: {registry: d, repository: r}, "
            "imports: [{name: v, tags: {}}]}\n"
        )


def test_parse_rejects_non_mapping() -> None:
    with pytest.raises(PolicyValidationError, match="mapping"):
        parse_mirror_policy("- just\n- a\n- list\n")


def test_parse_rejects_invalid_yaml() -> None:
    with pytest.raises(PolicyValidationError, match="YAML"):
        parse_mirror_policy("key: : :\n")


def test_parse_wraps_validation_error() -> None:
    with pytest.raises(PolicyValidationError):
        parse_mirror_policy(
            "apiVersion: knock.io/v1alpha1\nkind: MirrorPolicy\nmetadata: {name: x}\n"
            "spec: {source: {registry: d, repository: r}, imports: [{name: v, tags: {}}]}\n"
        )


def test_json_schema_is_emitted_with_camel_case_keys() -> None:
    schema = mirror_policy_json_schema()
    assert schema["title"] == "MirrorPolicy"
    assert schema["type"] == "object"
    # camelCase property names (the public contract), not snake_case
    dumped = repr(schema)
    assert "artifactType" in dumped
    assert "includeRegex" in dumped
    assert "artifact_type" not in dumped


def test_json_schema_is_stable_and_serializable() -> None:
    import json

    # must be JSON-serializable (publishable for editor/CI validation)
    json.dumps(mirror_policy_json_schema())


_BASE = """
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
spec:
  artifactType: image
  source:
    registry: docker.io
    repository: library/redis
  {deletion_mode_line}
  imports:
    - name: stable
      tags:
        includeRegex: "^7\\\\."
"""


def test_deletion_mode_defaults_to_none() -> None:
    policy = parse_mirror_policy(_BASE.format(deletion_mode_line=""))
    assert policy.spec.deletion_mode is None


def test_deletion_mode_parses_mark() -> None:
    policy = parse_mirror_policy(_BASE.format(deletion_mode_line="deletionMode: mark"))
    assert policy.spec.deletion_mode is DeletionMode.mark


def test_owners_parsed_on_import_and_defaults() -> None:
    policy = parse_mirror_policy(
        "apiVersion: knock.io/v1alpha1\nkind: MirrorPolicy\nmetadata: {name: x}\n"
        "spec:\n"
        "  artifactType: image\n"
        "  source: {registry: docker.io, repository: library/redis}\n"
        "  defaults: {owners: ['group:default/platform']}\n"
        "  imports:\n"
        "    - name: v7\n"
        "      tags: {}\n"
        "      owners: ['group:default/payments', 'group:default/data']\n"
    )
    assert policy.spec.defaults.owners == ["group:default/platform"]
    assert policy.spec.imports[0].owners == ["group:default/payments", "group:default/data"]


def test_short_owner_form_accepted() -> None:
    policy = parse_mirror_policy(
        "apiVersion: knock.io/v1alpha1\nkind: MirrorPolicy\nmetadata: {name: x}\n"
        "spec:\n"
        "  artifactType: image\n"
        "  source: {registry: docker.io, repository: library/redis}\n"
        "  imports: [{name: v, tags: {}, owners: ['payments']}]\n"
    )
    assert policy.spec.imports[0].owners == ["payments"]


def test_malformed_owner_rejected() -> None:
    with pytest.raises(PolicyValidationError):
        parse_mirror_policy(
            "apiVersion: knock.io/v1alpha1\nkind: MirrorPolicy\nmetadata: {name: x}\n"
            "spec:\n"
            "  artifactType: image\n"
            "  source: {registry: docker.io, repository: library/redis}\n"
            "  imports: [{name: v, tags: {}, owners: ['bad owner!']}]\n"
        )
