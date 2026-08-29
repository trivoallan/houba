"""Stamping a source-derived artifact: no base image, an honest revision."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knock.domain.coverage import is_stamped
from knock.domain.stamp import build_git_stamp_annotations
from knock.errors import PolicyValidationError, exit_code_for

CREATED = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
REVISION = "c0ffee" + "0" * 34


def stamp(
    *,
    prefix: str = "io.knock",
    url: str = "https://github.com/example/agent-skill.git",
    revision: str = REVISION,
    title: str = "agent-skill",
    created: datetime = CREATED,
    owners: list[str] | None = None,
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
        owners=owners if owners is not None else ["group:default/platform"],
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


def test_the_result_reads_back_as_stamped() -> None:
    assert is_stamped(stamp(), prefix="io.knock") is True


def test_vendor_is_omitted_when_absent() -> None:
    assert "org.opencontainers.image.vendor" not in stamp()
    assert stamp(vendor="Example Platform Team")["org.opencontainers.image.vendor"] == (
        "Example Platform Team"
    )


def test_stamps_exactly_the_expected_keys() -> None:
    # Whole-contract test: the mutations that survived line/branch coverage (a dropped
    # title, `created` written under the wrong namespace, a spurious extra key) all
    # change the key *set*, even when no single-key assertion above would notice.
    assert set(stamp()) == {
        "org.opencontainers.image.title",
        "org.opencontainers.image.source",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.created",
        "io.knock.artifact.type",
        "io.knock.policy",
        "io.knock.import",
        "io.knock.variant",
        "io.knock.owners",
    }


def test_empty_prefix_refuses_rather_than_emit_an_unreadable_stamp() -> None:
    # With no base image to anchor coverage.is_stamped's empty-prefix fallback, a
    # base-less stamp under an empty prefix would carry only standard OCI keys —
    # indistinguishable from an unstamped artifact. Refuse instead of fabricating
    # a stamp that can never read back as covered.
    with pytest.raises(PolicyValidationError):
        stamp(prefix="")


def test_empty_prefix_error_exits_1_like_any_domain_error() -> None:
    with pytest.raises(PolicyValidationError) as exc_info:
        stamp(prefix="")
    assert exit_code_for(exc_info.value) == 1


def test_empty_revision_is_refused_as_fabrication_by_emptiness() -> None:
    with pytest.raises(PolicyValidationError):
        stamp(revision="")
