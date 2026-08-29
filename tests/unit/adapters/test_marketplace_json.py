"""Projecting stamped artifacts into a marketplace manifest."""

from __future__ import annotations

import pytest

from knock.adapters.marketplace_json import (
    ManifestDropError,
    PublishedSkill,
    build_marketplace,
)


def skill(name: str = "probe", digest: str = "sha256:" + "a" * 64) -> PublishedSkill:
    return PublishedSkill(
        name=name,
        blob_url=f"https://registry.example/v2/skills/{name}/blobs/{digest}",
        sha256=digest.removeprefix("sha256:"),
    )


def test_builds_a_manifest_with_archive_sources() -> None:
    doc = build_marketplace("internal", "Platform Team", [skill()])
    assert doc["name"] == "internal"
    assert doc["owner"] == {"name": "Platform Team"}
    entry = doc["plugins"][0]
    assert entry["name"] == "probe"
    assert entry["source"]["source"] == "archive"
    assert entry["source"]["sha256"] == "a" * 64


def test_every_entry_carries_a_sha256() -> None:
    # The client verifies integrity only when the field is present, so its absence is a
    # silent hole. Nothing downstream enforces this; the projection must.
    doc = build_marketplace("internal", "Platform Team", [skill("a"), skill("b")])
    assert all(p["source"].get("sha256") for p in doc["plugins"])


def test_entries_are_sorted_so_the_document_is_reproducible() -> None:
    doc = build_marketplace("internal", "Platform Team", [skill("z"), skill("a")])
    assert [p["name"] for p in doc["plugins"]] == ["a", "z"]


def test_rejects_a_drop_against_the_previous_count() -> None:
    with pytest.raises(ManifestDropError, match="2 to 1"):
        build_marketplace("internal", "Platform Team", [skill("a")], previous_count=2)


def test_a_drop_is_allowed_when_explicitly_confirmed() -> None:
    doc = build_marketplace(
        "internal", "Platform Team", [skill("a")], previous_count=2, allow_drop=True
    )
    assert len(doc["plugins"]) == 1


def test_growth_and_equality_need_no_confirmation() -> None:
    grown = build_marketplace("i", "o", [skill("a"), skill("b")], previous_count=1)
    assert len(grown["plugins"]) == 2
    steady = build_marketplace("i", "o", [skill("a")], previous_count=1)
    assert len(steady["plugins"]) == 1


def test_an_empty_projection_is_a_drop_not_an_empty_catalog() -> None:
    with pytest.raises(ManifestDropError):
        build_marketplace("internal", "Platform Team", [], previous_count=1)
