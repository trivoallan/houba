"""The pure purge decision: idle-window arithmetic + observation → decision (§5).

Stays free of ports types (codebase invariant: domain never imports ports). The
use case bridges the oracle's `UsageObservation` into these primitives — mirroring
how `use_cases/reconcile_registry.py` bridges `ImageInfo` via `to_source_artifact`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum


class PurgeDecision(StrEnum):
    purge = "purge"
    protect = "protect"
    uncertain = "uncertain"


def usage_window_start(now: datetime, min_idle: timedelta) -> datetime:
    """The oracle look-back lower bound: anything seen at/after this is still in use."""
    return now - min_idle


def decide_purge(last_seen: datetime | None, *, observed: bool) -> PurgeDecision:
    """`observed=False` (oracle errored) ⇒ uncertain (fail-closed to protect in the
    use case); a prod sighting (`last_seen` set) ⇒ protect; observed but none ⇒ purge."""
    if not observed:
        return PurgeDecision.uncertain
    if last_seen is not None:
        return PurgeDecision.protect
    return PurgeDecision.purge
