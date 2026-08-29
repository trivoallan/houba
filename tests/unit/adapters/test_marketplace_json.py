"""Projecting stamped artifacts into a marketplace manifest."""

from __future__ import annotations

import dataclasses

import pytest

from knock.adapters.marketplace_json import (
    DuplicateSkillNameError,
    MalformedDigestError,
    ManifestDropError,
    PublishedSkill,
    build_marketplace,
)
from knock.errors import exit_code_for


def skill(name: str = "probe", digest: str = "sha256:" + "a" * 64) -> PublishedSkill:
    return PublishedSkill(
        name=name,
        blob_url=f"https://registry.example/v2/skills/{name}/blobs/{digest}",
        sha256=digest.removeprefix("sha256:"),
    )


def test_builds_a_manifest_with_archive_sources() -> None:
    doc = build_marketplace("internal", "Platform Team", [skill()], previous_count=0)
    assert doc["name"] == "internal"
    assert doc["owner"] == {"name": "Platform Team"}
    entry = doc["plugins"][0]
    assert entry["name"] == "probe"
    assert entry["source"]["source"] == "archive"
    assert entry["source"]["sha256"] == "a" * 64


def test_document_matches_the_client_schema_exactly() -> None:
    # Field-by-field assertions catch renames and removals but not additions. This is the
    # only place the exact contract is written down — the consuming client can't be
    # exercised from this repo.
    doc = build_marketplace("internal", "Platform Team", [skill("probe")], previous_count=0)
    assert doc == {
        "name": "internal",
        "owner": {"name": "Platform Team"},
        "plugins": [
            {
                "name": "probe",
                "source": {
                    "source": "archive",
                    "url": "https://registry.example/v2/skills/probe/blobs/sha256:" + "a" * 64,
                    "sha256": "a" * 64,
                },
            }
        ],
    }


def test_every_entry_carries_a_sha256() -> None:
    # The client verifies integrity only when the field is present, so its absence is a
    # silent hole. Nothing downstream enforces this; the projection must.
    doc = build_marketplace("internal", "Platform Team", [skill("a"), skill("b")], previous_count=0)
    assert all(p["source"].get("sha256") for p in doc["plugins"])


def test_entries_are_sorted_so_the_document_is_reproducible() -> None:
    doc = build_marketplace("internal", "Platform Team", [skill("z"), skill("a")], previous_count=0)
    assert [p["name"] for p in doc["plugins"]] == ["a", "z"]


def test_previous_count_zero_allows_any_first_publish() -> None:
    doc = build_marketplace("internal", "Platform Team", [skill("a"), skill("b")], previous_count=0)
    assert len(doc["plugins"]) == 2


def test_omitting_previous_count_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        build_marketplace("internal", "Platform Team", [skill()])  # type: ignore[call-arg]


def test_rejects_a_drop_against_the_previous_count() -> None:
    with pytest.raises(ManifestDropError, match="2 to 1"):
        build_marketplace("internal", "Platform Team", [skill("a")], previous_count=2)


def test_a_drop_is_allowed_when_explicitly_confirmed() -> None:
    doc = build_marketplace(
        "internal", "Platform Team", [skill("a")], previous_count=2, allow_drop=True
    )
    assert len(doc["plugins"]) == 1


def test_allow_drop_without_previous_count_is_rejected() -> None:
    # allow_drop with no previous_count to allow a drop against is inert protection at the
    # call site; it must fail loudly rather than being silently accepted.
    with pytest.raises(ManifestDropError, match="previous_count"):
        build_marketplace(
            "internal", "Platform Team", [skill()], previous_count=None, allow_drop=True
        )


def test_growth_needs_no_confirmation() -> None:
    grown = build_marketplace("i", "o", [skill("a"), skill("b")], previous_count=1)
    assert len(grown["plugins"]) == 2


def test_equal_count_needs_no_confirmation() -> None:
    steady = build_marketplace("i", "o", [skill("a")], previous_count=1)
    assert len(steady["plugins"]) == 1


def test_an_empty_projection_is_a_drop_not_an_empty_catalog() -> None:
    with pytest.raises(ManifestDropError):
        build_marketplace("internal", "Platform Team", [], previous_count=1)


def test_rejects_duplicate_skill_names() -> None:
    # Two skills sharing a name leave len(skills) unchanged even if a different skill was
    # dropped, so the drop guard alone cannot catch this — it must be rejected outright.
    dup = skill("dup")
    with pytest.raises(DuplicateSkillNameError, match="dup"):
        build_marketplace("internal", "Platform Team", [dup, dup], previous_count=2)


# -- sha256 validation at construction --------------------------------------------------
#
# The client's integrity check is `if (t.sha256 && ...)` — an empty string is falsy in
# JavaScript, so a present-but-empty sha256 skips verification exactly like a missing
# one. Validating at construction keeps a bad value from ever reaching build_marketplace.


def test_rejects_an_empty_sha256() -> None:
    with pytest.raises(MalformedDigestError, match="exactly 64"):
        PublishedSkill(name="probe", blob_url="https://x/blob", sha256="")


def test_rejects_a_sha256_of_the_wrong_length() -> None:
    with pytest.raises(MalformedDigestError, match="exactly 64"):
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


@pytest.mark.parametrize("bad", ["a" * 64 + "\n", " " + "a" * 63, "a" * 63 + " "])
def test_rejects_whitespace_padded_digests(bad: str) -> None:
    # Pins fullmatch over match: with `.match(...)` and an anchored-looking pattern, a
    # trailing newline or padding would slip through.
    with pytest.raises(MalformedDigestError):
        PublishedSkill(name="probe", blob_url="https://x/blob", sha256=bad)


def test_a_well_formed_digest_round_trips_unchanged() -> None:
    digest = "b" * 64
    doc = build_marketplace(
        "internal",
        "Platform Team",
        [PublishedSkill(name="probe", blob_url="https://x", sha256=digest)],
        previous_count=0,
    )
    assert doc["plugins"][0]["source"]["sha256"] == digest


def test_published_skill_is_frozen() -> None:
    s = skill()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.name = "changed"  # type: ignore[misc]


# -- exit codes ---------------------------------------------------------------------------
#
# exit_code_for walks the MRO and falls through to InternalError (exit 4, "bug") for
# anything that matches no branch root. These errors must land on DomainError (exit 1) —
# a drop-guard trip, a malformed digest, and a duplicate name are policy refusals, not
# crashes, and an operator's response to each must not be "file a bug".


def test_manifest_drop_error_exits_as_a_domain_error() -> None:
    assert exit_code_for(ManifestDropError("x")) == 1


def test_malformed_digest_error_exits_as_a_domain_error() -> None:
    assert exit_code_for(MalformedDigestError("x")) == 1


def test_duplicate_skill_name_error_exits_as_a_domain_error() -> None:
    assert exit_code_for(DuplicateSkillNameError("x")) == 1
