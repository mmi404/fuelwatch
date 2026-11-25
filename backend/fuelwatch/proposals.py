"""Proposals and alerts (design 5.1 and 3.3).

After every snapshot the allocator's plan is turned into one proposal per station and fuel, each with its
explanation card. A proposal that is still valid is left alone; one that is no longer what the allocator
would say is superseded; one that is too old, or belongs to a world that was reset, expires. Nothing
here sends anything to the simulator: approval and execution come later.
"""

from __future__ import annotations

import logging

from .allocate import Plan, Shipment
from .db import Database
from .explain import build_explanation
from .forecast import DemandForecaster
from .snapshot import WorldSnapshot
from .stockout import Assessment
from .world import ResetEvent, WorldTracker

log = logging.getLogger("fuelwatch.proposals")

POLICY = "greedy"
EXPIRY_TICKS = 40  # ten simulated hours; re-validation, not expiry, protects correctness (ADR-07)
SAME_ABS_LITERS = 500.0  # a refreshed plan within this many litres, or SAME_RELATIVE of the old total, is "the same"
SAME_RELATIVE = 0.15
OPEN = ("PROPOSED", "HELD")
ALERT_LEVELS = {"WATCH": 1, "WARNING": 2, "CRITICAL": 3}
ALERT_ACTIVE = ("open", "acknowledged")


def _same(old_lines: list[dict], new: list[Shipment]) -> bool:
    """The new plan for a pair uses the same depots and routes and about the same litres."""
    if {(l["depot_id"], l["route_id"]) for l in old_lines} != {(s.depot_id, s.route_id) for s in new}:
        return False
    old_total = sum(l["proposed_quantity"] for l in old_lines)
    new_total = sum(s.quantity for s in new)
    return abs(new_total - old_total) <= max(SAME_ABS_LITERS, SAME_RELATIVE * old_total)


class ProposalService:
    def __init__(self, db: Database, world: WorldTracker) -> None:
        self._db = db
        self._world = world
        world.on_reset(self._on_reset)

    def _on_reset(self, event: ResetEvent) -> None:
        """A reset ends every open proposal and alert of the old world."""
        for p in self._db.proposals(epoch_id=event.old_epoch_id, statuses=OPEN, limit=1000):
            self._db.set_proposal_status(p["id"], "EXPIRED", "world reset", only_from=OPEN)
        for a in self._db.alerts(epoch_id=event.old_epoch_id, states=ALERT_ACTIVE, limit=1000):
            self._db.set_alert_state(a["id"], "cleared")

    # -- proposals -----------------------------------------------------------------------

    def sync(self, snapshot: WorldSnapshot, forecaster: DemandForecaster, assessments: list[Assessment], plan: Plan) -> dict[str, int]:
        epoch = self._world.epoch
        if epoch is None:
            return {}
        T = snapshot.tick
        counts = {"created": 0, "kept": 0, "superseded": 0, "expired": 0}

        open_now = {(p["station_id"], p["fuel_type"]): p for p in self._db.proposals(epoch_id=epoch.id, statuses=("PROPOSED",), limit=1000)}
        for key, p in list(open_now.items()):
            if p["expires_tick"] <= T and self._db.set_proposal_status(p["id"], "EXPIRED", "expired", only_from=OPEN):
                counts["expired"] += 1
                del open_now[key]

        # A stale or torn snapshot must not create new advice; existing proposals simply wait.
        if snapshot.stale or not snapshot.consistent:
            return counts

        grouped: dict[tuple[str, str], list[Shipment]] = {}
        for s in plan.shipments:
            grouped.setdefault((s.station_id, s.fuel_type), []).append(s)
        by_pair = {(a.station_id, a.fuel_type): a for a in assessments}
        decisions = {(d.station_id, d.fuel_type): d for d in plan.decisions}
        accuracy = forecaster.accuracy()

        for key, p in open_now.items():
            if key not in grouped and self._db.set_proposal_status(p["id"], "SUPERSEDED", "no longer recommended", only_from=("PROPOSED",)):
                counts["superseded"] += 1

        for key, shipments in grouped.items():
            old = open_now.get(key)
            if old is not None and _same(old["lines"], shipments):
                counts["kept"] += 1
                continue
            card, score, risk_before, risk_after = build_explanation(
                snapshot, forecaster, by_pair[key], decisions.get(key), shipments, [f for f in plan.shortfalls if (f.station_id, f.fuel_type) == key], accuracy
            )
            pid = self._db.insert_proposal(
                epoch_id=epoch.id,
                station_id=key[0],
                fuel_type=key[1],
                snapshot_tick=T,
                expires_tick=T + EXPIRY_TICKS,
                policy=POLICY,
                confidence=score,
                risk_before=risk_before,
                risk_after=risk_after,
                explanation=card,
                lines=[{"depot_id": s.depot_id, "station_id": s.station_id, "route_id": s.route_id, "fuel_type": s.fuel_type, "quantity": s.quantity} for s in shipments],
            )
            counts["created"] += 1
            if old is not None and self._db.set_proposal_status(old["id"], "SUPERSEDED", f"replaced by proposal {pid}", only_from=("PROPOSED",)):
                counts["superseded"] += 1
        return counts

    def recent(self, *, statuses: tuple[str, ...] | None = None, limit: int = 100) -> list[dict]:
        epoch = self._world.epoch
        return self._db.proposals(epoch_id=epoch.id, statuses=statuses, limit=limit) if epoch else []

    def get(self, proposal_id: int) -> dict | None:
        return self._db.proposal(proposal_id)

    # -- alerts ---------------------------------------------------------------------------

    def sync_alerts(self, snapshot: WorldSnapshot, assessments: list[Assessment]) -> None:
        """One alert per station and fuel while it is at WATCH or worse; cleared when it is OK again."""
        epoch = self._world.epoch
        if epoch is None:
            return
        active = {(a["station_id"], a["fuel_type"]): a for a in self._db.alerts(epoch_id=epoch.id, states=ALERT_ACTIVE, limit=1000)}
        seen: set[tuple[str, str]] = set()
        for a in assessments:
            key = (a.station_id, a.fuel_type)
            if a.level not in ALERT_LEVELS:
                continue
            seen.add(key)
            existing = active.get(key)
            if existing is None:
                self._db.insert_alert(epoch_id=epoch.id, station_id=a.station_id, fuel_type=a.fuel_type, level=a.level, tick=snapshot.tick, hours_p50=a.hours_p50, hours_p90=a.hours_p90)
                continue
            self._db.refresh_alert(existing["id"], level=a.level, tick=snapshot.tick, hours_p50=a.hours_p50, hours_p90=a.hours_p90)
            if existing["state"] == "acknowledged" and ALERT_LEVELS[a.level] > ALERT_LEVELS[existing["level"]]:
                self._db.set_alert_state(existing["id"], "open")  # it got worse after being acknowledged
        for key, existing in active.items():
            if key not in seen and not any(a.station_id == key[0] and a.fuel_type == key[1] and a.level == "UNKNOWN" for a in assessments):
                self._db.set_alert_state(existing["id"], "cleared")

    def alerts(self, states: tuple[str, ...] | None = None) -> list[dict]:
        epoch = self._world.epoch
        return self._db.alerts(epoch_id=epoch.id, states=states) if epoch else []
