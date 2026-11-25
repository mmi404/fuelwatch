"""The single health mode the operator sees (design part 5.4).

Derived from the snapshot and the failure counters, never set by hand.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from .snapshot import WorldSnapshot


class Mode(StrEnum):
    STARTING = "STARTING"  # no snapshot yet
    LIVE = "LIVE"  # fresh and consistent
    DEGRADED = "DEGRADED"  # serving the last good snapshot while reads fail, or the data is flagged
    STALE = "STALE"  # snapshot older than the limit


def compute_mode(
    snapshot: WorldSnapshot | None,
    *,
    consecutive_failures: int,
    any_breaker_not_closed: bool,
    stale_after: float,
    now: datetime | None = None,
) -> Mode:
    if snapshot is None:
        return Mode.STARTING
    if snapshot.age_seconds(now) > stale_after:
        return Mode.STALE
    if consecutive_failures > 0 or any_breaker_not_closed or snapshot.stale or not snapshot.consistent:
        return Mode.DEGRADED
    return Mode.LIVE
