"""Projecting stamped artifacts into a marketplace manifest."""

from __future__ import annotations

import pytest

from knock.adapters.marketplace_json import (
    MalformedDigestError,
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


# -- sha256 validation at construction --------------------------------------------------
#
# The client's integrity check is `if (t.sha256 && ...)` — an empty string is falsy in
# JavaScript, so a present-but-empty sha256 skips verification exactly like a missing
# one. Validating at construction keeps a bad value from ever reaching build_marketplace.


def test_rejects_an_empty_sha256() -> None:
    with pytest.raises(MalformedDigestError, match="64"):
        PublishedSkill(name="probe", blob_url="https://x/blob", sha256="")


def test_rejects_a_sha256_of_the_wrong_length() -> None:
    with pytest.raises(MalformedDigestError, match="64"):
        PublishedSkill(name="probe", blob_url="https://x/blob", sha256="a" * 63)


def test_rejects_uppercase_hex() -> None:
    with pytest.raises(MalformedDigestError, match="lowercase"):
        PublishedSkill(name="probe", blob_url="https://x/blob", sha256="A" * 64)


def test_rejects_non_hex_characters() -> None:
    with pytest.raises(MalformedDigestError, match="hex"):
        PublishedSkill(name="probe", blob_url="https://x/blob", sha256="g" * 64)


def test_rejects_a_sha256_prefixed_digest() -> None:
    # The field is documented as bare hex; the OCI digest form carries the prefix, and a
    # caller copying a digest straight from a manifest will hand us `sha256:abc...`.
    with pytest.raises(MalformedDigestError, match="prefix"):
        PublishedSkill(name="probe", blob_url="https://x/blob", sha256="sha256:" + "a" * 64)


def test_a_well_formed_digest_round_trips_unchanged() -> None:
    digest = "b" * 64
    doc = build_marketplace(
        "internal",
        "Platform Team",
        [PublishedSkill(name="probe", blob_url="https://x", sha256=digest)],
    )
    assert doc["plugins"][0]["source"]["sha256"] == digest
