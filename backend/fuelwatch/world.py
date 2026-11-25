"""World epochs, reset detection and demand-history sync (design 4.9, ADR-09).

An evaluator can reset the simulator at any moment. A reset leaves the instance id,
scenario and seed exactly as they were (`[live]`), so the only signs are the tick
going backwards and the simulator's running counters going backwards. Each time a
snapshot is about to be published we compare it with the last thing seen; a
difference starts a new epoch, and everything persisted is keyed by epoch so ticks
from different worlds never mix.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .client import SimulatorClient
from .db import Database, EpochRow
from .errors import SimulatorError
from .models import FuelType
from .snapshot import WorldSnapshot

log = logging.getLogger("fuelwatch.world")

# Settle tolerance for the cumulative litres counter (it is a float sum).
_EPSILON = 1e-6
# The history endpoint returns at most this many rows, however large `limit` is (`[live]`: 2,000 rows
# is 166 ticks of 12). A station filter has its own cap, so a longer window is read one station at a time.
HISTORY_PAGE_CAP = 2000
# Assumed to apply to a station-filtered response too (`[inferred]`: 600 rows came back for 200 ticks, below the cap).
HISTORY_STATION_CAP = 2000


@dataclass(frozen=True)
class ResetEvent:
    reason: str
    detail: str
    old_epoch_id: int
    new_epoch_id: int


def detect_reset(prev: EpochRow, snap: WorldSnapshot) -> tuple[str, str] | None:
    """Compare a new snapshot with the last observation. Returns (reason, detail) or None."""
    inst = snap.instance
    if snap.tick < prev.last_tick:
        return "tick_regression", f"tick {prev.last_tick} -> {snap.tick}"
    if (inst.id, inst.scenario_id, inst.scenario_version, inst.seed) != (prev.instance_id, prev.scenario_id, prev.scenario_version, prev.seed):
        return "scenario_change", f"{prev.scenario_id}/{prev.seed} -> {inst.scenario_id}/{inst.seed}"
    if inst.tick_minutes != prev.tick_minutes:
        return "scenario_change", f"tick_minutes {prev.tick_minutes} -> {inst.tick_minutes}"
    if len(snap.allocations) < prev.last_allocation_count:
        return "counters_regression", f"allocations {prev.last_allocation_count} -> {len(snap.allocations)}"
    if snap.metrics.served_demand_liters < prev.last_served_liters - _EPSILON:
        return "counters_regression", f"served litres {prev.last_served_liters:.1f} -> {snap.metrics.served_demand_liters:.1f}"
    return None


class WorldTracker:
    """Owns the current epoch and keeps its demand history complete."""

    def __init__(self, db: Database, client: SimulatorClient) -> None:
        self._db = db
        self._client = client
        self._epoch: EpochRow | None = db.latest_epoch()
        self.resets_total = 0
        self.last_reset: ResetEvent | None = None
        self.history_error: str | None = None
        self.synced_through = -1
        self.history_from: int | None = None
        self.history_rows = 0
        self._listeners: list[Callable[[ResetEvent], None]] = []

    @property
    def epoch(self) -> EpochRow | None:
        return self._epoch

    def on_reset(self, listener: Callable[[ResetEvent], None]) -> None:
        """Register a callback for a detected reset (forecaster caches, proposals)."""
        self._listeners.append(listener)

    def observe(self, snap: WorldSnapshot) -> ResetEvent | None:
        """Run before a snapshot is published: detect a reset and record the observation."""
        inst = snap.instance
        reason_detail: tuple[str, str] | None
        if self._epoch is None:
            reason_detail = ("first_seen", "no earlier epoch")
        else:
            reason_detail = detect_reset(self._epoch, snap)

        if reason_detail is None:
            assert self._epoch is not None
            self._db.update_epoch_observation(
                self._epoch.id, tick=snap.tick, allocation_count=len(snap.allocations), served_liters=snap.metrics.served_demand_liters
            )
            self._epoch = EpochRow(**{**self._epoch.__dict__, "last_tick": snap.tick, "last_allocation_count": len(snap.allocations), "last_served_liters": snap.metrics.served_demand_liters})
            return None

        reason, detail = reason_detail
        old = self._epoch
        self._epoch = self._db.insert_epoch(
            reason=reason,
            instance_id=inst.id,
            scenario_id=inst.scenario_id,
            scenario_version=inst.scenario_version,
            seed=inst.seed,
            tick_minutes=inst.tick_minutes,
            tick=snap.tick,
            allocation_count=len(snap.allocations),
            served_liters=snap.metrics.served_demand_liters,
        )
        self.synced_through = -1
        self.history_from = None
        self.history_rows = 0
        if old is None:
            log.info("world epoch %d started (%s)", self._epoch.id, reason)
            return None
        event = ResetEvent(reason, detail, old.id, self._epoch.id)
        self.resets_total += 1
        self.last_reset = event
        log.warning("world reset detected: %s (%s); epoch %d -> %d", reason, detail, old.id, self._epoch.id)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:  # a broken listener must not stop ingestion
                log.exception("reset listener failed")
        return event

    def _rows_per_tick(self, snap: WorldSnapshot) -> int:
        return max(1, len(snap.stations) * len(FuelType))

    async def _fetch_history(self, snap: WorldSnapshot, missing: int, rpt: int) -> list:
        limit = (missing + 1) * rpt  # the gap plus one tick of slack for a tick that finished after the snapshot
        if limit <= HISTORY_PAGE_CAP:
            return await self._client.demand_history(limit=limit)
        per_station = min((missing + 1) * len(FuelType), HISTORY_STATION_CAP)
        pages = await asyncio.gather(
            *(self._client.demand_history(station_id=st.id, limit=per_station) for st in snap.stations),
            return_exceptions=True,
        )
        for page in pages:
            if isinstance(page, BaseException):
                raise page
        return [row for page in pages for row in page]

    async def sync_history(self, snap: WorldSnapshot) -> int:
        """Fetch the demand rows we are missing for the current epoch. Returns rows stored.

        The history endpoint is newest-first, has no `since` filter and caps one response at
        2,000 rows, so the request is sized from what is missing: the gap after the newest
        complete tick. A gap that survives one sync widens the next. Past the cap the window is
        read station by station.
        """
        epoch = self._epoch
        if epoch is None:
            return 0
        rpt = self._rows_per_tick(snap)
        try:
            run = await asyncio.to_thread(self._db.complete_run, epoch.id, rpt)
            newest = run[1] if run else -1
            missing = (snap.tick - 1) - newest
            stored = 0
            if missing > 0:
                records = await self._fetch_history(snap, missing, rpt)
                if self._epoch is None or self._epoch.id != epoch.id:
                    return 0  # a reset was detected while we were fetching: these rows belong to the old world
                stored = await asyncio.to_thread(self._db.upsert_demand, epoch.id, records)
                run = await asyncio.to_thread(self._db.complete_run, epoch.id, rpt)
            self.synced_through = run[1] if run else -1
            self.history_from = run[0] if run else None
            self.history_rows = await asyncio.to_thread(self._db.demand_row_count, epoch.id)
            self.history_error = None
            return stored
        except SimulatorError as exc:
            self.history_error = str(exc)
            log.warning("demand history sync failed: %s", exc)
            return 0

    def info(self) -> dict[str, object]:
        e = self._epoch
        return {
            "epoch": None if e is None else {"id": e.id, "reason": e.reason, "started_at": e.started_at},
            "resets_total": self.resets_total,
            "last_reset": None if self.last_reset is None else {"reason": self.last_reset.reason, "detail": self.last_reset.detail},
            "history": {"from_tick": self.history_from, "synced_through_tick": self.synced_through, "rows": self.history_rows, "error": self.history_error},
        }
