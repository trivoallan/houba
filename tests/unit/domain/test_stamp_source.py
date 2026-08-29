"""Stamping a source-derived artifact: no base image, an honest revision."""

from __future__ import annotations

from datetime import UTC, datetime

from knock.domain.coverage import is_stamped
from knock.domain.stamp import build_source_stamp_annotations

CREATED = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


def stamp(**overrides: object) -> dict[str, str]:
    kwargs: dict[str, object] = {
        "prefix": "io.knock",
        "origin": "https://github.com/example/agent-skill.git",
        "revision": "c0ffee" + "0" * 34,
        "title": "agent-skill",
        "created": CREATED,
        "owners": ["group:default/platform"],
        "vendor": None,
        "artifact_type": "skill",
        "policy": "example-skill",
        "import_name": "release",
        "variant": "",
    }
    kwargs.update(overrides)
    return build_source_stamp_annotations(**kwargs)  # type: ignore[arg-type]


def test_omits_the_base_image_keys() -> None:
    annotations = stamp()
    assert "org.opencontainers.image.base.name" not in annotations
    assert "org.opencontainers.image.base.digest" not in annotations


def test_revision_is_the_upstream_commit() -> None:
    assert stamp()["org.opencontainers.image.revision"] == "c0ffee" + "0" * 34


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
