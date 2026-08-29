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

PublishedSkill validates its sha256 at construction, for a related reason: the client's
integrity check is `if (t.sha256 && ...)`, and an empty string is falsy in JavaScript — a
present-but-empty sha256 would skip verification exactly like a missing one. Rejecting a
malformed digest as early as the caller's mistake keeps it from ever reaching the document.

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

from knock.errors import DomainError

# No `^`/`$` anchors: the pattern is only ever used with `fullmatch`, which anchors on its
# own. Anchoring here too would make `.fullmatch(...)` look interchangeable with the
# unanchored `.match(...)` — which would silently let a trailing newline or padding through.
_HEX64 = re.compile(r"[0-9a-f]{64}")


class ManifestDropError(DomainError):
    """The projection would publish fewer entries than the previous manifest.

    Rooted in `DomainError` (exit 1), not `KnockError` directly: `exit_code_for` falls
    through to `InternalError` (exit 4, "bug") for anything that matches no branch root,
    and a drop-guard trip is a policy refusal, not a crash — the two must not share an
    exit code.
    """


class MalformedDigestError(DomainError):
    """A `PublishedSkill.sha256` is not a bare, 64-character lowercase hex digest.

    Also a `DomainError` (exit 1): a malformed caller-supplied digest is a validation
    failure, not an internal bug.
    """


class DuplicateSkillNameError(DomainError):
    """Two published skills share a name.

    The manifest cannot represent both without silently collapsing one — and because the
    entry count is unchanged, the drop guard cannot see the loss. Rejected outright rather
    than deduplicated, so the caller's registry walk gets a loud error instead of a quietly
    smaller catalog.
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
    `previous_count` to allow a drop against, and is rejected rather than silently ignored.
    """
    if allow_drop and previous_count is None:
        raise ManifestDropError(
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
