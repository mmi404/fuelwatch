"""The immutable world snapshot and the store that holds it.

One poller builds a ``WorldSnapshot`` per tick. Everything downstream (the
dashboard API, the forecaster, the allocator) reads that one object, so they
all see the same consistent world and never touch the simulator themselves.
The store swaps the reference atomically; readers never see a half-built world.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from .models import (
    Allocation,
    AllocationStatus,
    Depot,
    Event,
    Instance,
    Metrics,
    Region,
    Route,
    Station,
    SupplyArrival,
)

# Allocations whose fuel has left the depot but not yet reached the station.
_ON_THE_ROAD = (AllocationStatus.PENDING, AllocationStatus.IN_TRANSIT)


class WorldSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    instance: Instance
    regions: tuple[Region, ...]
    depots: tuple[Depot, ...]
    stations: tuple[Station, ...]
    routes: tuple[Route, ...]
    supply_arrivals: tuple[SupplyArrival, ...]
    events: tuple[Event, ...]
    allocations: tuple[Allocation, ...]
    metrics: Metrics
    fetched_at: datetime
    # The simulator sent X-Simulator-Stale: true on at least one read.
    stale: bool = False
    # The tick did not move while we were reading. False means a torn read.
    consistent: bool = True

    @property
    def tick(self) -> int:
        return self.instance.tick

    @property
    def tick_minutes(self) -> int:
        return self.instance.tick_minutes

    @property
    def in_transit(self) -> tuple[Allocation, ...]:
        """Fuel already committed to a station: PENDING and IN_TRANSIT."""
        return tuple(a for a in self.allocations if a.status in _ON_THE_ROAD)

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        return max(0.0, (now - self.fetched_at).total_seconds())


class SnapshotStore:
    """Holds the latest good snapshot. A failed refresh never replaces it."""

    def __init__(self) -> None:
        self._current: WorldSnapshot | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0

    @property
    def current(self) -> WorldSnapshot | None:
        return self._current

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def publish(self, snapshot: WorldSnapshot) -> None:
        self._current = snapshot  # single reference assignment: atomic
        self._last_error = None
        self._consecutive_failures = 0

    def record_failure(self, error: str) -> None:
        self._last_error = error
        self._consecutive_failures += 1
