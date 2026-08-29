"""Stamping a source-derived artifact: no base image, an honest revision."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knock.domain.coverage import is_stamped
from knock.domain.stamp import build_git_stamp_annotations
from knock.errors import ConfigError, PolicyValidationError, exit_code_for

CREATED = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
REVISION = "c0ffee" + "0" * 34

# A module-level constant, not a mutable default argument: the helper below never
# mutates `owners`, and a literal-list default is what `ruff` (B006) forbids — this
# dodges that without collapsing the None-vs-provided distinction (see stamp()).
_DEFAULT_OWNERS: list[str] = ["group:default/platform"]


def stamp(
    *,
    prefix: str = "io.knock",
    url: str = "https://github.com/example/agent-skill.git",
    revision: str = REVISION,
    title: str = "agent-skill",
    created: datetime = CREATED,
    owners: list[str] | None = _DEFAULT_OWNERS,
    vendor: str | None = None,
    artifact_type: str = "skill",
    policy: str = "example-skill",
    import_name: str = "release",
    variant: str = "default",
) -> dict[str, str]:
    return build_git_stamp_annotations(
        prefix=prefix,
        url=url,
        revision=revision,
        title=title,
        created=created,
        owners=owners,
        vendor=vendor,
        artifact_type=artifact_type,
        policy=policy,
        import_name=import_name,
        variant=variant,
    )


def test_omits_the_base_image_keys() -> None:
    annotations = stamp()
    assert "org.opencontainers.image.base.name" not in annotations
    assert "org.opencontainers.image.base.digest" not in annotations


def test_revision_is_the_upstream_commit() -> None:
    assert stamp()["org.opencontainers.image.revision"] == REVISION


def test_source_is_the_upstream_url() -> None:
    assert stamp()["org.opencontainers.image.source"] == (
        "https://github.com/example/agent-skill.git"
    )


def test_carries_the_knock_lineage() -> None:
    annotations = stamp()
    assert annotations["io.knock.policy"] == "example-skill"
    assert annotations["io.knock.artifact.type"] == "skill"
    assert annotations["io.knock.owners"] == "group:default/platform"


def test_owners_omitted_when_none() -> None:
    # owners=None must reach build_git_stamp_annotations unchanged — the default above
    # is a fixture convenience, not a translation, so this path is actually exercised.
    assert "io.knock.owners" not in stamp(owners=None)


def test_the_result_reads_back_as_stamped() -> None:
    assert is_stamped(stamp(), prefix="io.knock") is True


def test_vendor_is_omitted_when_absent() -> None:
    assert "org.opencontainers.image.vendor" not in stamp()
    assert stamp(vendor="Example Platform Team")["org.opencontainers.image.vendor"] == (
        "Example Platform Team"
    )


def test_stamps_exactly_these_annotations() -> None:
    # Whole-contract test: exact key/value equality, not just a key-set check. A
    # key-set-only assertion let four value mutations through unnoticed (title<->url,
    # variant<->import_name, import<->policy swaps, and — the sharp one — a builder
    # that stamps the same hardcoded `created` timestamp for every artifact).
    assert stamp() == {
        "org.opencontainers.image.title": "agent-skill",
        "org.opencontainers.image.source": "https://github.com/example/agent-skill.git",
        "org.opencontainers.image.revision": REVISION,
        "org.opencontainers.image.created": "2026-08-29T12:00:00+00:00",
        "io.knock.artifact.type": "skill",
        "io.knock.policy": "example-skill",
        "io.knock.import": "release",
        "io.knock.variant": "default",
        "io.knock.owners": "group:default/platform",
    }


def test_empty_prefix_refuses_rather_than_emit_an_unreadable_stamp() -> None:
    # With no base image to anchor coverage.is_stamped's empty-prefix fallback, a
    # base-less stamp under an empty prefix would carry only standard OCI keys —
    # indistinguishable from an unstamped artifact. Refuse instead of fabricating a
    # stamp that can never read back as covered. KNOCK_LABEL_PREFIX is environment
    # configuration, not the operator's MirrorPolicy, so this is a ConfigError.
    with pytest.raises(ConfigError):
        stamp(prefix="")


def test_empty_prefix_error_exits_3_like_any_config_error() -> None:
    with pytest.raises(ConfigError) as exc_info:
        stamp(prefix="")
    assert exit_code_for(exc_info.value) == 3


def test_empty_prefix_error_names_the_variable() -> None:
    # An operator reading the error must be able to tell what to change.
    with pytest.raises(ConfigError, match="KNOCK_LABEL_PREFIX"):
        stamp(prefix="")


def test_empty_revision_is_refused_as_fabrication_by_emptiness() -> None:
    # Unlike the empty prefix above, this is a data fault (the resolved revision
    # itself is wrong), not a config fault, so it stays a PolicyValidationError.
    with pytest.raises(PolicyValidationError):
        stamp(revision="")
