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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from knock.errors import KnockError

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ManifestDropError(KnockError):
    """The projection would publish fewer entries than the previous manifest."""


class MalformedDigestError(KnockError):
    """A `PublishedSkill.sha256` is not a bare, 64-character lowercase hex digest."""


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
    previous_count: int | None = None,
    allow_drop: bool = False,
) -> dict[str, Any]:
    """Build the manifest document, or raise if it would shrink without confirmation."""
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
            for s in sorted(skills, key=lambda s: s.name)
        ],
    }
