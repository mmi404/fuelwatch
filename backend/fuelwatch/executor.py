"""Execution: gate, re-validation, idempotent send and tracking (design 4.3, 4.8, 5.1, 7.3, 7.4).

Approval is an upper bound. When an operator approves a proposal, the executor takes a fresh
snapshot, checks the gate, re-validates every line against the live world (the quantity can only
shrink), stores the quantity and the idempotency key, and only then sends the request. Every
outcome, including a skipped line, is written to the ledger and the audit log, and an unknown
outcome is resolved later by looking for the key in the simulator's allocation list. The system
never guesses.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import Counter
from dataclasses import asdict, dataclass
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .allocate import disrupted_route_ids, headroom_liters
from .client import SimulatorClient
from .config import Settings
from .db import Database
from .errors import CircuitOpenError, MalformedResponse, SimulatorError, SimulatorInvalidRequest, SimulatorNotFound, SimulatorRejected, SimulatorUnavailable
from .health import Mode
from .metrics import NOOP
from .models import AllocationRequest, AllocationStatus, StationStatus
from .snapshot import SnapshotStore, WorldSnapshot
from .stockout import inbound_by_tick, outage_ticks
from .world import ResetEvent, WorldTracker

if TYPE_CHECKING:
    from .events import EventBus
    from .intel import Intelligence
    from .poller import Poller

log = logging.getLogger("fuelwatch.executor")

MIN_SHIPMENT = 1000.0  # smaller shipments are not worth sending, unless half the approved quantity is smaller
LOOKUP_WINDOW_SECONDS = 10.0  # a request cannot still be in flight this long after it was sent (the client gives up sooner)
TERMINAL_LINES = ("SKIPPED", "REJECTED", "FAILED", "ARRIVED")
_SENT = ("SUBMITTED", "IN_TRANSIT", "ARRIVED")


@dataclass
class Autopilot:
    """Automatic approval within guard limits (ADR-07). Off at every start: a restart returns to manual."""

    enabled: bool = False
    min_confidence: float = 0.5  # Medium or better; Low-confidence proposals stay for a human
    max_liters_per_proposal: float = 20000.0
    max_liters_per_tick: float = 60000.0
    max_proposals_per_tick: int = 10


class ApiError(Exception):
    """An error the API turns into the unified error body."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Executor:
    def __init__(
        self,
        db: Database,
        world: WorldTracker,
        store: SnapshotStore,
        client: SimulatorClient,
        intel: Intelligence,
        settings: Settings,
        mode_fn: Callable[[], Mode],
    ) -> None:
        self._db, self._world, self._store, self._client = db, world, store, client
        self._intel, self._settings, self._mode = intel, settings, mode_fn
        self._poller: Poller | None = None
        self._lock = asyncio.Lock()  # one execution at a time: they share depot stock and dispatch capacity
        self._inflight: set[int] = set()
        self._last_retry_read: datetime | None = None
        self.counters: Counter[str] = Counter()
        self.autopilot = Autopilot()
        self.obs = NOOP
        self.bus: EventBus | None = None
        self._autopilot_tick: int | None = None
        self._autopilot_used = [0.0, 0]  # litres and proposals approved in the current tick
        world.on_reset(self._on_reset)

    def attach(self, poller: Poller) -> None:
        self._poller = poller

    def _publish(self, proposal_id: int) -> None:
        if self.bus is not None:
            p = self._db.proposal(proposal_id)
            if p is not None:
                self.bus.publish("proposal", {"id": proposal_id, "status": p["status"], "station_id": p["station_id"], "fuel_type": p["fuel_type"], "reason": p["status_reason"]})

    def set_autopilot(self, changes: dict[str, Any], actor: str = "operator") -> Autopilot:
        for key, value in changes.items():
            setattr(self.autopilot, key, value)
        self._db.audit(actor, "autopilot.changed", "autopilot", "autopilot", asdict(self.autopilot))
        if self.bus is not None:
            self.bus.publish("autopilot", asdict(self.autopilot))
        return self.autopilot

    # -- operator actions ------------------------------------------------------------------

    async def approve(self, proposal_id: int, *, override_stale: bool = False, actor: str = "operator") -> dict[str, Any]:
        async with self._lock:
            proposal = self._db.proposal(proposal_id)
            if proposal is None:
                raise ApiError(404, "NOT_FOUND", f"no proposal {proposal_id}")
            if proposal["status"] not in ("PROPOSED", "HELD"):
                raise ApiError(409, "INVALID_STATE", f"proposal {proposal_id} is {proposal['status']}, not open")
            if not self._db.set_proposal_status(proposal_id, "APPROVED", None, only_from=("PROPOSED", "HELD")):
                raise ApiError(409, "INVALID_STATE", f"proposal {proposal_id} changed state")
            self._db.audit(actor, "proposal.approved", "proposal", proposal_id, {"override_stale": override_stale, "snapshot_tick": proposal["snapshot_tick"], "liters": sum(l["proposed_quantity"] for l in proposal["lines"])})
            self.counters["approved"] += 1
            self.obs.proposal("APPROVED", id=proposal_id)
            result = await self._run(proposal_id, override_stale=override_stale, actor=actor)
            self._publish(proposal_id)
            return result

    async def reject(self, proposal_id: int, *, actor: str = "operator") -> dict[str, Any]:
        async with self._lock:
            proposal = self._db.proposal(proposal_id)
            if proposal is None:
                raise ApiError(404, "NOT_FOUND", f"no proposal {proposal_id}")
            if not self._db.set_proposal_status(proposal_id, "REJECTED", "rejected by operator", only_from=("PROPOSED", "HELD")):
                raise ApiError(409, "INVALID_STATE", f"proposal {proposal_id} is {proposal['status']}, not open")
            self._db.audit(actor, "proposal.rejected", "proposal", proposal_id, {})
            self.counters["rejected"] += 1
            self.obs.proposal("REJECTED", id=proposal_id)
            self._publish(proposal_id)
            return self._db.proposal(proposal_id)  # type: ignore[return-value]

    # -- the gate ---------------------------------------------------------------------------

    def _gate_reason(self, snap: WorldSnapshot | None, override_stale: bool) -> str | None:
        """Why execution is closed right now, or None when it is open."""
        mode = self._mode()
        if snap is None or mode is Mode.STARTING:
            return "no snapshot yet"
        if mode is Mode.STALE:
            return f"data is {snap.age_seconds():.0f} s old"
        if self._client.breaker_states()["write"]["state"] == "OPEN":
            return "the write circuit breaker is open"
        if mode is Mode.DEGRADED and not override_stale:
            detail = self._store.last_error or ("the simulator flagged its data stale" if snap.stale else "the last read was inconsistent" if not snap.consistent else "a circuit breaker is not closed")
            return f"degraded: {detail}"
        return None

    # -- the run ------------------------------------------------------------------------------

    async def _refresh(self) -> None:
        if self._poller is not None:
            self._poller.request_refresh()
            await self._poller.poll_once()

    async def _run(self, proposal_id: int, *, override_stale: bool, actor: str) -> dict[str, Any]:
        await self._refresh()
        snap, epoch = self._store.current, self._world.epoch
        proposal = self._db.proposal(proposal_id)
        assert proposal is not None

        reason = self._gate_reason(snap, override_stale)
        if reason is not None:
            self._hold(proposal_id, reason)
            return self._db.proposal(proposal_id)  # type: ignore[return-value]
        assert snap is not None and epoch is not None
        if override_stale and self._mode() is Mode.DEGRADED:
            self._db.audit(actor, "gate.override", "proposal", proposal_id, {"mode": "DEGRADED", "snapshot_tick": snap.tick})
        if proposal["epoch_id"] != epoch.id or snap.tick >= proposal["expires_tick"]:
            why = "world reset" if proposal["epoch_id"] != epoch.id else "expired"
            self._db.set_proposal_status(proposal_id, "EXPIRED", why, only_from=("APPROVED", "HELD"))
            self._db.audit("system", "proposal.expired", "proposal", proposal_id, {"reason": why, "tick": snap.tick})
            self.obs.proposal("EXPIRED", id=proposal_id, reason=why)
            return self._db.proposal(proposal_id)  # type: ignore[return-value]

        self._db.set_proposal_status(proposal_id, "EXECUTING", None, only_from=("APPROVED",))
        state = _RunState(snap)
        for line in proposal["lines"]:
            if line["status"] != "PLANNED":
                continue
            outcome = await self._execute_line(snap, proposal, line, state)
            if outcome == "HELD":
                self._hold(proposal_id, "the write circuit breaker opened during execution")
                return self._db.proposal(proposal_id)  # type: ignore[return-value]
        if self._poller is not None:
            self._poller.request_refresh()
        self._finalize(proposal_id)
        return self._db.proposal(proposal_id)  # type: ignore[return-value]

    def _hold(self, proposal_id: int, reason: str) -> None:
        if self._db.set_proposal_status(proposal_id, "HELD", reason, only_from=("APPROVED", "HELD", "EXECUTING")):
            self._db.audit("system", "proposal.held", "proposal", proposal_id, {"reason": reason})
            self.counters["held"] += 1
            self.obs.proposal("HELD", reason=reason, id=proposal_id)

    def _skip(self, proposal: dict, line: dict, reason: str) -> str:
        self._db.update_line(line["id"], status="SKIPPED", failure_code=reason)
        self._db.audit("system", "line.skipped", "proposal_line", line["id"], {"proposal_id": proposal["id"], "reason": reason, "approved_liters": line["proposed_quantity"]})
        self.counters["lines_skipped"] += 1
        self.obs.execution("execution.outcome", logging.WARNING, line_id=line["id"], proposal_id=proposal["id"], outcome="SKIPPED", code=reason)
        return "SKIPPED"

    def _revalidate(self, snap: WorldSnapshot, line: dict, state: _RunState) -> tuple[str | None, int]:
        """The litres this line may still send now, or the reason it may not (design 4.8)."""
        route = next((r for r in snap.routes if r.id == line["route_id"]), None)
        station = next((s for s in snap.stations if s.id == line["station_id"]), None)
        depot = next((d for d in snap.depots if d.id == line["depot_id"]), None)
        if route is None or station is None or depot is None:
            return "NOT_FOUND", 0
        if route.source_depot_id != depot.id or route.destination_station_id != station.id:
            return "ROUTE_MISMATCH", 0
        if route.id in disrupted_route_ids(snap):
            return "ROUTE_DISRUPTED", 0
        if station.status is StationStatus.OUTAGE:
            return "STATION_CLOSED", 0
        fuel = next((f for f in station.capacity if f.value == line["fuel_type"]), None)
        if fuel is None:
            return "NOT_FOUND", 0

        approved = float(line["proposed_quantity"])
        arrivals = dict(inbound_by_tick(snap, station.id, fuel))
        for t, q in state.sent_to.get((station.id, fuel.value), {}).items():
            arrivals[t] = arrivals.get(t, 0.0) + q
        capacity, inventory = float(station.capacity.get(fuel, 0.0)), float(station.inventory.get(fuel, 0.0))
        forecaster = self._intel.forecaster
        if forecaster.ready(station.id, fuel.value):
            headroom = headroom_liters(snap, forecaster, station, fuel, route, arrivals, outage_ticks(station, snap, snap.tick, 96))
        else:  # no forecast: the physical room, counting everything on the road
            headroom = max(0.0, capacity - inventory - sum(arrivals.values()))
        limits = {
            "approved": approved,
            "route_max": float(route.max_shipment),
            "depot_stock": float(depot.inventory.get(fuel, 0.0)) - state.depot_sent.get((depot.id, fuel.value), 0.0),
            "dispatch_capacity": state.dispatch_left(snap, depot.id, float(depot.dispatch_capacity_per_tick)),
            "headroom": headroom,
        }
        binding = min(limits, key=limits.get)
        quantity = math.floor(max(0.0, limits[binding]))
        floor = min(MIN_SHIPMENT, max(1.0, 0.5 * approved))
        if quantity < floor:
            return ("BELOW_MINIMUM" if binding == "approved" else binding.upper()), 0
        return None, quantity

    async def _execute_line(self, snap: WorldSnapshot, proposal: dict, line: dict, state: _RunState) -> str:
        reason, quantity = self._revalidate(snap, line, state)
        if reason is not None:
            return self._skip(proposal, line, reason)

        # The quantity is stored before anything is sent; a retry always sends this same pair.
        self._db.update_line(line["id"], status="SENDING", executed_quantity=float(quantity))
        self._inflight.add(line["id"])
        self._db.add_attempt(line["id"], snapshot_tick=snap.tick, snapshot_age_s=snap.age_seconds(), http_status=None, error_code=None, outcome="SENDING")
        self.obs.execution("execution.attempt", line_id=line["id"], proposal_id=proposal["id"], key=line["idempotency_key"], liters=quantity, approved_liters=line["proposed_quantity"])
        request = AllocationRequest(idempotency_key=line["idempotency_key"], source_depot_id=line["depot_id"], destination_station_id=line["station_id"], route_id=line["route_id"], fuel_type=line["fuel_type"], quantity=float(quantity))
        age = snap.age_seconds()
        http_status: int | None = None
        error_code: str | None = None
        try:
            allocation = await self._client.create_allocation(request)
        except CircuitOpenError:
            # Nothing was sent: the line goes back to PLANNED and the proposal is held.
            self._db.update_line(line["id"], status="PLANNED", executed_quantity=None)
            self._inflight.discard(line["id"])
            return "HELD"
        except (SimulatorRejected, SimulatorInvalidRequest, SimulatorNotFound) as exc:
            http_status, error_code = exc.status, exc.code or type(exc).__name__
            self._db.update_line(line["id"], status="REJECTED", failure_code=error_code)
            self._db.add_attempt(line["id"], snapshot_tick=snap.tick, snapshot_age_s=age, http_status=http_status, error_code=error_code, outcome="REJECTED")
            self._db.audit("system", "line.rejected", "proposal_line", line["id"], {"proposal_id": proposal["id"], "code": error_code, "liters": quantity})
            self.counters["lines_rejected"] += 1
            self.obs.allocation_outcome("rejected_409")
            self.obs.execution("execution.outcome", logging.WARNING, line_id=line["id"], proposal_id=proposal["id"], outcome="REJECTED", code=error_code, liters=quantity)
            self._inflight.discard(line["id"])
            return "REJECTED"
        except (SimulatorUnavailable, MalformedResponse, SimulatorError) as exc:
            http_status, error_code = getattr(exc, "status", None), getattr(exc, "code", None) or type(exc).__name__
            self._db.update_line(line["id"], status="UNKNOWN", failure_code=error_code)
            self._db.add_attempt(line["id"], snapshot_tick=snap.tick, snapshot_age_s=age, http_status=http_status, error_code=error_code, outcome="UNKNOWN")
            self._db.audit("system", "line.unknown", "proposal_line", line["id"], {"proposal_id": proposal["id"], "error": str(exc), "liters": quantity})
            state.record_sent(line, quantity, snap.tick, self._route_ticks(snap, line))  # it may have gone through
            self.counters["lines_unknown"] += 1
            self.obs.execution("execution.outcome", logging.WARNING, line_id=line["id"], proposal_id=proposal["id"], outcome="UNKNOWN", code=error_code, liters=quantity)
            self._inflight.discard(line["id"])
            return "UNKNOWN"

        self._db.update_line(line["id"], status="SUBMITTED", sim_allocation_id=allocation.id, failure_code=None)
        self._db.add_attempt(line["id"], snapshot_tick=snap.tick, snapshot_age_s=age, http_status=201, error_code=None, outcome="SUBMITTED")
        self._db.audit("system", "line.submitted", "proposal_line", line["id"], {"proposal_id": proposal["id"], "allocation_id": allocation.id, "liters": quantity, "approved_liters": line["proposed_quantity"]})
        state.record_sent(line, quantity, snap.tick, self._route_ticks(snap, line))
        self.counters["lines_submitted"] += 1
        self.obs.execution("execution.outcome", line_id=line["id"], proposal_id=proposal["id"], outcome="SUBMITTED", allocation_id=allocation.id, liters=quantity)
        self._inflight.discard(line["id"])
        return "SUBMITTED"

    @staticmethod
    def _route_ticks(snap: WorldSnapshot, line: dict) -> int:
        return next((r.transit_ticks for r in snap.routes if r.id == line["route_id"]), 0)

    def _finalize(self, proposal_id: int) -> None:
        """Close an EXECUTING proposal once none of its lines is still open (design 5.1)."""
        proposal = self._db.proposal(proposal_id)
        if proposal is None or proposal["status"] != "EXECUTING":
            return
        lines = proposal["lines"]
        if any(l["status"] in ("PLANNED", "SENDING", "UNKNOWN") for l in lines):
            return
        sent = sum(1 for l in lines if l["sim_allocation_id"] is not None)
        status = "EXECUTED" if sent == len(lines) else "PARTIAL" if sent else "FAILED"
        if self._db.set_proposal_status(proposal_id, status, None, only_from=("EXECUTING",)):
            self._db.audit("system", f"proposal.{status.lower()}", "proposal", proposal_id, {"lines": len(lines), "sent": sent})
            self.counters[f"proposal_{status.lower()}"] += 1
            self.obs.proposal(status, id=proposal_id)

    # -- after every snapshot ---------------------------------------------------------------------

    async def on_snapshot(self, snap: WorldSnapshot) -> None:
        """Resolve unknown outcomes and follow submitted lines. Never raises."""
        epoch = self._world.epoch
        if epoch is None:
            return
        try:
            by_key = {a.idempotency_key: a for a in snap.allocations}
            for line in self._db.lines_in(("SENDING", "UNKNOWN"), epoch_id=epoch.id):
                if line["id"] in self._inflight:
                    continue
                found = by_key.get(line["idempotency_key"])
                if found is not None:
                    self._db.update_line(line["id"], status="SUBMITTED", sim_allocation_id=found.id, failure_code=None)
                    self._db.audit("system", "line.resolved", "proposal_line", line["id"], {"allocation_id": found.id, "how": "found by idempotency key"})
                    self.obs.execution("execution.unknown_resolved", line_id=line["id"], allocation_id=found.id, how="found by idempotency key")
                    continue
                sent_at = self._db.last_attempt_at(line["id"])
                # The request cannot still be in flight once a snapshot read after the lookup window
                # has no allocation carrying the key; only then is the line known to have failed.
                if sent_at is not None and snap.fetched_at >= datetime.fromisoformat(sent_at) + timedelta(seconds=LOOKUP_WINDOW_SECONDS):
                    self._db.update_line(line["id"], status="FAILED", failure_code="NOT_FOUND_AFTER_LOOKUP")
                    self._db.audit("system", "line.failed", "proposal_line", line["id"], {"reason": "no allocation carries the key after the lookup window"})
                    self.obs.allocation_outcome("failed")
                    self.obs.execution("execution.unknown_resolved", logging.WARNING, line_id=line["id"], how="not found after the lookup window")
            for line in self._db.lines_in(("SUBMITTED", "IN_TRANSIT"), epoch_id=epoch.id):
                found = by_key.get(line["idempotency_key"])
                if found is None:
                    continue
                new = {AllocationStatus.PENDING: "SUBMITTED", AllocationStatus.IN_TRANSIT: "IN_TRANSIT", AllocationStatus.ARRIVED: "ARRIVED", AllocationStatus.FAILED: "FAILED", AllocationStatus.CANCELLED: "FAILED"}[found.status]
                if new != line["status"]:
                    if new == "ARRIVED":
                        self.obs.allocation_outcome("arrived")
                    elif new == "FAILED":
                        self.obs.allocation_outcome("cancelled" if found.status is AllocationStatus.CANCELLED else "failed")
                    self._db.update_line(line["id"], status=new, **({"failure_code": found.failure_reason or found.status.value} if new == "FAILED" else {}))
            for p in self._db.proposals(epoch_id=epoch.id, statuses=("EXECUTING",), limit=100):
                self._finalize(p["id"])
        except Exception:
            self.obs.ledger_error()
            log.exception("ledger tracking failed")

    async def after_poll(self) -> None:
        """Once per read of the world: run held approvals again if the gate reopened, then the autopilot. Never raises."""
        snap, epoch = self._store.current, self._world.epoch
        if snap is None or epoch is None or self._lock.locked() or snap.fetched_at == self._last_retry_read:
            return
        self._last_retry_read = snap.fetched_at  # once per read, not per tick: the gate can reopen while the tick stands still
        try:
            await self._resume_held(epoch.id)
            await self._run_autopilot(epoch.id)
        except Exception:
            log.exception("after-poll execution step failed")

    async def _resume_held(self, epoch_id: int) -> None:
        for p in reversed(self._db.proposals(epoch_id=epoch_id, statuses=("HELD",), limit=50)):  # oldest first
            async with self._lock:
                current = self._db.proposal(p["id"])
                if current is None or current["status"] != "HELD" or self._gate_reason(self._store.current, False) is not None:
                    continue
                if not self._db.set_proposal_status(p["id"], "APPROVED", None, only_from=("HELD",)):
                    continue
                self._db.audit("system", "proposal.resumed", "proposal", p["id"], {})
                await self._run(p["id"], override_stale=False, actor="system")
                self._publish(p["id"])

    async def _run_autopilot(self, epoch_id: int) -> None:
        ap = self.autopilot
        snap = self._store.current
        if not ap.enabled or snap is None or self._gate_reason(snap, False) is not None:
            return
        if self._autopilot_tick != snap.tick:
            self._autopilot_tick, self._autopilot_used = snap.tick, [0.0, 0]
        open_now = self._db.proposals(epoch_id=epoch_id, statuses=("PROPOSED",), limit=100)

        def urgency(p: dict) -> float:
            hours = p["explanation"].get("headline", {}).get("hours_p90")
            return float(hours) if hours is not None else 1e9

        for p in sorted(open_now, key=urgency):
            liters = float(sum(l["proposed_quantity"] for l in p["lines"]))
            if p["confidence"] < ap.min_confidence or liters > ap.max_liters_per_proposal:
                self.counters["autopilot_skipped_guard"] += 1
                continue
            if self._autopilot_used[1] >= ap.max_proposals_per_tick or self._autopilot_used[0] + liters > ap.max_liters_per_tick:
                self.counters["autopilot_skipped_budget"] += 1
                continue
            try:
                await self.approve(p["id"], actor="autopilot")
            except ApiError:
                continue  # it changed state under us: another actor decided it
            self._autopilot_used[0] += liters
            self._autopilot_used[1] += 1
            self.counters["autopilot_approved"] += 1

    def _on_reset(self, event: ResetEvent) -> None:
        """Whatever the old world was doing is over: nothing in it can be confirmed any more."""
        for line in self._db.lines_in(("PLANNED",), epoch_id=event.old_epoch_id):
            self._db.update_line(line["id"], status="SKIPPED", failure_code="WORLD_RESET")
        for line in self._db.lines_in(("SENDING", "UNKNOWN", "SUBMITTED", "IN_TRANSIT"), epoch_id=event.old_epoch_id):
            self._db.update_line(line["id"], status="FAILED", failure_code="WORLD_RESET")
        for p in self._db.proposals(epoch_id=event.old_epoch_id, statuses=("APPROVED", "HELD", "EXECUTING"), limit=1000):
            if p["status"] == "EXECUTING":
                self._finalize(p["id"])
            else:
                self._db.set_proposal_status(p["id"], "EXPIRED", "world reset", only_from=("APPROVED", "HELD"))


class _RunState:
    """What this run has already used, since the snapshot cannot show it yet."""

    def __init__(self, snap: WorldSnapshot) -> None:
        self.tick = snap.tick
        self.depot_sent: dict[tuple[str, str], float] = {}
        self.dispatch_used: dict[str, float] = {}
        self.sent_to: dict[tuple[str, str], dict[int, float]] = {}
        self._created_this_tick: dict[str, float] = {}
        for a in snap.allocations:
            if a.created_tick == snap.tick and a.status is not AllocationStatus.CANCELLED:
                self._created_this_tick[a.source_depot_id] = self._created_this_tick.get(a.source_depot_id, 0.0) + a.quantity

    def dispatch_left(self, snap: WorldSnapshot, depot_id: str, capacity: float) -> float:
        return max(0.0, capacity - self._created_this_tick.get(depot_id, 0.0) - self.dispatch_used.get(depot_id, 0.0))

    def record_sent(self, line: dict, quantity: float, tick: int, transit: int) -> None:
        key = (line["depot_id"], line["fuel_type"])
        self.depot_sent[key] = self.depot_sent.get(key, 0.0) + quantity
        self.dispatch_used[line["depot_id"]] = self.dispatch_used.get(line["depot_id"], 0.0) + quantity
        target = self.sent_to.setdefault((line["station_id"], line["fuel_type"]), {})
        target[tick + transit] = target.get(tick + transit, 0.0) + quantity
