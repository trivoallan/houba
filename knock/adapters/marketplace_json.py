"""Project stamped skill artifacts into a marketplace manifest. Pure, but an adapter.

No I/O, yet it belongs in `adapters/`: it encodes one named consumer's document shape, which
is the only place in knock that knows a specific client exists. Decision 10A keeps that
coupling here so the domain never acquires it.

A projection, never stored state: it is derived from what the registry holds on every call,
so it cannot drift from the registry. The drop guard exists because the dangerous failure
here is not a crash, it is a catalog that quietly gets smaller.

    stamped artifacts ──▶ build_marketplace ──▶ manifest document
                                 │
                                 └── fewer entries than last time? ──▶ ManifestDropError

PublishedSkill validates its sha256 and blob_url at construction, for a shared reason: both
values come from the registry walk (see the class docstring), not from operator input, so a
malformed one means the registry returned garbage — there is no MirrorPolicy field to fix.
That is why both errors below root at AdapterError, not DomainError.

The client's integrity check is `if (t.sha256 && ...)`, and an empty string is falsy in
JavaScript — a present-but-empty sha256 would skip verification exactly like a missing one.
The client's manifest validation is a single schema `safeParse` over the whole document, so
one malformed blob_url fails validation of the *entire* manifest, not just that entry — every
skill becomes uninstallable from one bad row, a strictly worse instance of the failure class
the drop guard exists to catch. Rejecting both at construction keeps a bad value from ever
reaching build_marketplace.

Why the drop guard lives here, not in the domain: "the catalog must not shrink without
confirmation" is knock's own safety policy, not a detail of this client's JSON shape, so it
belongs above the format-specific adapter layer in principle. It stays here because the use
case that would compose it — the thing that would call `build_marketplace` and hold the guard
above it — does not exist yet; hoisting a policy into a task with no composer is speculative.
Move it out the day a second consumer format needs the same guard: duplicating it per adapter
is bad, but a second adapter silently missing it is worse, because the omission is invisible.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from knock.errors import AdapterError, DomainError

# No `^`/`$` anchors: the pattern is only ever used with `fullmatch`, which anchors on its
# own. Anchoring here too would make `.fullmatch(...)` look interchangeable with the
# unanchored `.match(...)` — which would silently let a trailing newline or padding through.
_HEX64 = re.compile(r"[0-9a-f]{64}")

# The client's manifest validation rejects `archive` sources served over plain http, and over
# https from localhost specifically (verified against the spec) — both fail at manifest
# validation, not at download, so letting either through here would still take down the whole
# catalog at install time, just later than necessary.
_LOCALHOST_HOSTS = {"localhost"}


class ManifestDropError(DomainError):
    """The projection would publish fewer entries than the previous manifest.

    Rooted in `DomainError` (exit 1), not `KnockError` directly: `exit_code_for` falls
    through to `InternalError` (exit 4, "bug") for anything that matches no branch root,
    and a drop-guard trip is a policy refusal, not a crash — the two must not share an
    exit code. A drop refusal is resolved by the operator's own `allow_drop`, so it is
    correctly operator-actionable, unlike the registry-sourced errors below.
    """


class MalformedDigestError(AdapterError):
    """A `PublishedSkill.sha256` is not a bare, 64-character lowercase hex digest.

    An `AdapterError` (exit 2), not a `DomainError`: `sha256` comes from the registry walk,
    never from a `MirrorPolicy` field an operator wrote, so a malformed value means the
    registry returned garbage — exit 1 would send an operator to fix a policy that
    contains nothing to fix.
    """


class MalformedBlobUrlError(AdapterError):
    """A `PublishedSkill.blob_url` is not a plain `https://` URL with a non-localhost host.

    Also an `AdapterError` (exit 2), for the same reason as `MalformedDigestError`: the URL
    comes from the registry walk, not from operator input. The bar is deliberately narrow —
    scheme plus a non-empty, non-localhost host, not a full URL parser — because the failure
    this guards against is not a malformed link, it is the client's whole-document schema
    validation rejecting the entire manifest over one bad row.
    """


class DuplicateSkillNameError(DomainError):
    """Two published skills share a name, by exact string comparison.

    The manifest cannot represent both without silently collapsing one — and because the
    entry count is unchanged, the drop guard cannot see the loss. Rejected outright rather
    than deduplicated, so the caller's registry walk gets a loud error instead of a quietly
    smaller catalog. Names trace back to operator-authored import names, so this stays a
    `DomainError` (exit 1) unlike the two registry-sourced errors above.

    Limit: comparison is exact string equality. It will not catch `Probe`/`probe` (case),
    a trailing-space variant, or Unicode NFC vs. NFD forms of the same visible name (e.g.
    two byte-distinct but visually identical spellings of "café") — a human reading the
    manifest cannot tell those apart, but this check does not either. Whether the
    consuming client case-folds or Unicode-normalizes plugin names before matching is not
    established; until it is, treat this as a narrow, defensible check rather than a
    guarantee that "share a name" covers every way two names can collide.
    """


@dataclass(frozen=True)
class PublishedSkill:
    """One stamped skill artifact, as the registry walk found it."""

    name: str
    blob_url: str
    sha256: str  # bare hex, no `sha256:` prefix — the shape the client expects

    def __post_init__(self) -> None:
        if self.sha256.startswith("sha256:"):
            raise MalformedDigestError(
                f"{self.name}: sha256 must be bare hex, without the 'sha256:' prefix "
                f"(got {self.sha256!r})"
            )
        if not _HEX64.fullmatch(self.sha256):
            raise MalformedDigestError(
                f"{self.name}: sha256 must be exactly 64 lowercase hex characters "
                f"(got {self.sha256!r})"
            )

        parsed = urlsplit(self.blob_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise MalformedBlobUrlError(
                f"{self.name}: blob_url must be an https:// URL with a host (got {self.blob_url!r})"
            )
        if parsed.hostname.lower() in _LOCALHOST_HOSTS:
            raise MalformedBlobUrlError(
                f"{self.name}: blob_url must not point at localhost (got {self.blob_url!r})"
            )


def build_marketplace(
    name: str,
    owner: str,
    skills: list[PublishedSkill],
    *,
    previous_count: int | None,
    allow_drop: bool = False,
) -> dict[str, Any]:
    """Build the manifest document, or raise if it would shrink without confirmation.

    `previous_count` is a required keyword (no default): a forgetful caller gets a
    `TypeError` instead of silently publishing an unguarded first catalog. Pass `0`
    explicitly for a genuine first publish, or `None` when the previous count could not be
    determined — `None` disables the guard, so `allow_drop` is meaningless without a
    `previous_count` to allow a drop against, and raises `ValueError` rather than being
    silently ignored: this is a call-shape mistake, not a policy refusal a caller can
    confirm away, so it must not be catchable as `ManifestDropError`.

    Limit: the guard compares counts, not identities, so it cannot see substitution. If a
    skill loses its gating verdict in the same run a different skill is newly added,
    `len(skills)` is unchanged, the guard does not fire, and the dropped skill silently
    disappears from every workstation — the catalog never got smaller. Seeing that needs
    `previous_names: frozenset[str]` in place of a bare count; the signature stays `int |
    None` here because there is no caller yet to supply the prior name set, and fetching one
    speculatively belongs with the use case that will actually call this function.
    """
    if allow_drop and previous_count is None:
        raise ValueError(
            "allow_drop has no effect without previous_count; pass previous_count "
            "explicitly (0 for a first publish) or drop allow_drop"
        )

    names = Counter(s.name for s in skills)
    duplicates = sorted(n for n, count in names.items() if count > 1)
    if duplicates:
        raise DuplicateSkillNameError(
            f"duplicate skill name(s) in projection: {duplicates!r}; the manifest cannot "
            "represent them without silently collapsing one"
        )

    if previous_count is not None and not allow_drop and len(skills) < previous_count:
        raise ManifestDropError(
            f"projection would shrink the catalog from {previous_count} to {len(skills)} "
            "entries; pass allow_drop to confirm"
        )
    return {
        "name": name,
        "owner": {"name": owner},
        "plugins": [
            {
                "name": s.name,
                "source": {
                    "source": "archive",
                    "url": s.blob_url,
                    # Always emitted. The client skips verification entirely when this key
                    # is absent, so omitting it would silently disable integrity checking.
                    "sha256": s.sha256,
                },
            }
            for s in sorted(skills, key=lambda skill: skill.name)
        ],
    }
