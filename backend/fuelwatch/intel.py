"""The intelligence service: keeps the forecaster trained and the assessments current.

Runs once per published snapshot, after the demand history is synced, and never in a request
path: the API only reads the latest result. A failure leaves the previous assessments in place.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

from .allocate import Plan
from .anomaly import Anomaly, AnomalyDetector
from .db import Database
from .forecast import Z90, DemandForecaster, hour_of_day
from .metrics import NOOP
from .planner import plan_with_fallback
from .snapshot import WorldSnapshot
from .stockout import Assessment, assess
from .world import ResetEvent, WorldTracker

if TYPE_CHECKING:
    from .proposals import ProposalService

log = logging.getLogger("fuelwatch.intel")


class Intelligence:
    def __init__(self, db: Database, world: WorldTracker, forecaster: DemandForecaster | None = None, proposals: "ProposalService | None" = None, allocator: str = "greedy") -> None:
        self._allocator = allocator
        self._db = db
        self._proposals = proposals
        self.plan: Plan | None = None
        self.proposal_counts: dict[str, int] = {}
        self.obs = NOOP
        self._world = world
        self.forecaster = forecaster or DemandForecaster()
        self.detector = AnomalyDetector()
        self.anomalies: tuple[Anomaly, ...] = ()
        self.forecaster.on_station_tick = self._observe_demand
        self.assessments: tuple[Assessment, ...] = ()
        self.assessed_tick: int | None = None
        self.snapshot: WorldSnapshot | None = None
        self.last_error: str | None = None
        self.failed_stage: str | None = None  # "forecast" or "plan": the step that failed in the latest update
        world.on_reset(self._on_reset)

    def _on_reset(self, _: ResetEvent) -> None:
        self.forecaster.reset()
        self.detector.reset()
        self.anomalies = ()
        self.assessments = ()
        self.assessed_tick = None
        self.plan = None
        self.proposal_counts = {}
        self.failed_stage = None

    def _observe_demand(self, *args) -> None:
        try:
            self.detector.observe(*args)
        except Exception:  # anomaly detection is optional: it must never stop the forecaster learning
            log.exception("anomaly observation failed")

    def _detect(self, snap: WorldSnapshot) -> None:
        try:
            self.anomalies = self.detector.evaluate(snap)
        except Exception:  # no anomaly list is better than an old one, and nothing else depends on it
            self.anomalies = ()
            log.exception("anomaly detection failed")

    async def update(self, snap: WorldSnapshot) -> None:
        epoch = self._world.epoch
        if epoch is None:
            return
        try:
            first = max(self.forecaster.trained_through + 1, self._world.history_from or 0)
            last = self._world.synced_through
            if last >= first:
                raw = await asyncio.to_thread(self._db.demand_rows, epoch.id, from_tick=first)
                rows = [(r["station_id"], r["fuel_type"], r["tick"], r["demand"]) for r in raw if r["tick"] <= last]
                self.forecaster.train(rows, snap.stations, snap.regions, snap.events, snap.tick_minutes)
            decided = time.perf_counter()
            self.assessments = tuple(assess(snap, self.forecaster))
            self.assessed_tick = snap.tick
            self.snapshot = snap
            self.last_error = None
        except Exception as exc:  # the previous assessments stay in place
            self.last_error = repr(exc)
            self.failed_stage = "forecast"
            log.exception("intelligence update failed")
            return
        self._detect(snap)
        if self._proposals is None:
            self.failed_stage = None
            return
        try:
            assessments = list(self.assessments)
            self.plan, _ = await asyncio.to_thread(plan_with_fallback, snap, self.forecaster, assessments, self._allocator, self.obs)
            self.obs.decision(self.plan.policy, time.perf_counter() - decided)
            self.proposal_counts = await asyncio.to_thread(self._sync_proposals, snap, assessments, self.plan)
            self.failed_stage = None
        except Exception as exc:  # advice is optional: a failure here never hides the risk view
            self.last_error = repr(exc)
            self.failed_stage = "plan"
            self.obs.ledger_error()
            log.exception("proposal update failed")

    def _sync_proposals(self, snap: WorldSnapshot, assessments: list[Assessment], plan: Plan) -> dict[str, int]:
        assert self._proposals is not None
        self._proposals.sync_alerts(snap, assessments)
        unexplained = {a.station_id: a.summary for a in self.anomalies if a.kind == "unexplained"}
        return self._proposals.sync(snap, self.forecaster, assessments, plan, unexplained)

    def forecast_rows(self, station_id: str | None = None, fuel: str | None = None, ticks: int = 24) -> list[dict[str, object]]:
        """Per-tick demand forecast with a P10 to P90 band for the next `ticks` ticks."""
        snap = self.snapshot
        if snap is None:
            return []
        rows: list[dict[str, object]] = []
        for station in snap.stations:
            if station_id and station.id != station_id:
                continue
            for fuel_type in station.capacity:
                if fuel and fuel_type.value != fuel:
                    continue
                series = self.forecaster._series.get((station.id, fuel_type.value))  # noqa: SLF001
                if series is None:
                    continue
                sigma = series.sigma()
                for i in range(ticks):
                    t = snap.tick + i
                    p50 = self.forecaster.tick_forecast(station, fuel_type.value, t, snap.tick_minutes, snap.events)
                    if p50 is None:
                        continue
                    spread = Z90 * sigma * p50
                    rows.append({"station_id": station.id, "fuel_type": fuel_type.value, "tick": t, "hour": hour_of_day(t, snap.tick_minutes), "p10": max(0.0, p50 - spread), "p50": p50, "p90": p50 + spread})
        return rows

    def open_anomalies(self) -> list[dict[str, object]]:
        return [asdict(a) for a in self.anomalies]

    def risks(self) -> list[dict[str, object]]:
        return [asdict(a) for a in self.assessments]
