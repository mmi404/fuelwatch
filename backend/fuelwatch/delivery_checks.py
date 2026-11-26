"""The two checks of design 6.5 that are not about demand: late supply deliveries and fuel lost on arrival. Pure logic, no I/O.

Late delivery. A `shipment_delay` event moves an arrival's `planned_tick` itself, so the first planned tick
seen is remembered. A delivery is allowed that tick plus the delay of every started `shipment_delay` event
that covers it; delays are permanent, so an event's end does not take the allowance away. An arrival first
seen already delivered is history and is never judged. One first seen after a delay moved it gets the delay
allowed twice, which can hide a late delivery but cannot invent one.

Lost fuel. The simulator reports no delivered quantity, so it follows from conservation of station stock
between two snapshots at ticks t0 and t1, which have processed every tick before their own:
received = stock(t1) - stock(t0) + demand served in ticks t0 to t1 - 1. Only arrivals add to a station's
stock and only served demand takes from it. A pair is judged only when the served litres of every one of
those ticks are known; anything that does not add up is dropped rather than reported.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import AllocationStatus, Event, EventType, SupplyArrival
from .snapshot import WorldSnapshot

KEEP_TICKS = 32  # a finished finding stays for eight simulated hours at 15-minute ticks
MAX_SPAN_TICKS = 16  # a longer gap between snapshots, or a longer wait for demand history, is not judged
LOSS_MIN_LITERS = 50.0
LOSS_MIN_SHARE = 0.01  # of the quantity that arrived

Pair = tuple[str, str]  # station id, fuel


@dataclass(frozen=True)
class Finding:
    id: str
    kind: str  # late_delivery or lost_fuel
    state: str  # overdue, arrived_late or lost
    place_id: str  # the depot of a late delivery, the station of a loss
    place_name: str
    fuel_type: str
    litres: float  # the delivery's quantity, or the litres lost
    ticks_late: int | None
    first_tick: int  # the tick the delivery was due, or the arrival tick of the loss
    last_tick: int  # the newest tick the finding speaks about
    summary: str


@dataclass(frozen=True)
class _Interval:
    """One pair between two snapshots in which shipments arrived."""

    pair: Pair
    station_name: str
    t0: int
    t1: int
    gained: float  # stock(t1) - stock(t0)
    sent: float
    allocation_ids: tuple[int, ...]
    arrival_tick: int


def _covers(event: Event, arrival: SupplyArrival) -> bool:
    depots, fuels = event.parameters.get("depot_ids"), event.parameters.get("fuel_types")
    return (not depots or arrival.depot_id in depots) and (not fuels or arrival.fuel_type.value in fuels)


def _allowed_tick(first_planned: int, arrival: SupplyArrival, events: Sequence[Event], tick: int) -> int | None:
    """The latest tick the known delays account for, or None when a delay's size cannot be read."""
    allowed = first_planned
    for e in sorted(events, key=lambda e: (e.start_tick, e.id)):
        # An event changes only what is still undelivered when it starts.
        if e.type is not EventType.SHIPMENT_DELAY or e.status == "SCHEDULED" or e.start_tick > min(tick, allowed) or not _covers(e, arrival):
            continue
        delay = e.parameters.get("delay_ticks")
        if not isinstance(delay, int | float):
            return None
        allowed += max(0, int(delay))
    return allowed


class DeliveryChecker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Drop everything: a new world shares no history with the old one."""
        self._planned: dict[str, int] = {}  # first planned tick of each arrival seen while undelivered
        self._baseline: tuple[int, dict[Pair, float]] | None = None
        self._pending: list[_Interval] = []
        self._findings: dict[str, Finding] = {}
        self._tick = -1

    def history_needed_from(self) -> int | None:
        """The earliest tick whose served litres are still wanted, or None."""
        return min((i.t0 for i in self._pending), default=None)

    def observe(self, snap: WorldSnapshot) -> None:
        """One snapshot. A torn or repeated read is ignored."""
        if not snap.consistent or snap.tick < self._tick:
            return
        self._tick = snap.tick
        self._check_supply(snap)
        self._collect_arrivals(snap)
        self._findings = {k: f for k, f in self._findings.items() if f.state == "overdue" or snap.tick - f.last_tick <= KEEP_TICKS}

    def _check_supply(self, snap: WorldSnapshot) -> None:
        names = {d.id: d.name for d in snap.depots}
        for a in snap.supply_arrivals:
            key = f"supply:{a.id}"
            delivered = a.status == "ARRIVED"
            if not delivered:
                first = self._planned.setdefault(a.id, a.planned_tick)
            elif a.id in self._planned and a.actual_tick is not None:
                first = self._planned[a.id]
            else:
                continue
            allowed = _allowed_tick(first, a, snap.events, snap.tick)
            reached = a.actual_tick if delivered else snap.tick - 1  # the newest tick it had a chance to land in
            if delivered:
                del self._planned[a.id]  # judged once
            if allowed is None or reached is None or reached < allowed + (1 if delivered else 0):
                self._findings.pop(key, None)  # on time, or a delay that is now known explains it
                continue
            place = names.get(a.depot_id, a.depot_id)
            late = reached - allowed if delivered else snap.tick - allowed
            ticks = f"{late} tick{'' if late == 1 else 's'}"
            explained = f" (due tick {allowed} after the announced delay)" if allowed != first else f" (due tick {allowed})"
            what = f"{a.quantity:,.0f} L of {a.fuel_type.value.lower()} supply to {place}"
            summary = (
                f"{what} arrived at tick {reached}, {ticks} late{explained}. No shipment delay event accounts for it."
                if delivered
                else f"{what} is overdue by {ticks}{explained}. No shipment delay event accounts for it."
            )
            self._findings[key] = Finding(key, "late_delivery", "arrived_late" if delivered else "overdue", a.depot_id, place, a.fuel_type.value, a.quantity, late, allowed, reached, summary)

    def _collect_arrivals(self, snap: WorldSnapshot) -> None:
        stock = {(s.id, fuel.value): float(litres) for s in snap.stations for fuel, litres in s.inventory.items()}
        previous = self._baseline
        if previous is not None and snap.tick == previous[0]:
            return
        self._baseline = (snap.tick, stock)
        if previous is None or snap.tick - previous[0] > MAX_SPAN_TICKS:
            return
        t0, before = previous
        arrived = [a for a in snap.allocations if a.status is AllocationStatus.ARRIVED and a.actual_arrival_tick is not None and a.actual_arrival_tick >= t0]
        if any(a.actual_arrival_tick >= snap.tick for a in arrived):  # type: ignore[operator]
            return  # an arrival from a tick this snapshot should not have processed: the reads do not line up
        names = {s.id: s.name for s in snap.stations}
        for pair in sorted({(a.destination_station_id, a.fuel_type.value) for a in arrived}):
            if pair not in before or pair not in stock:
                continue
            mine = [a for a in arrived if (a.destination_station_id, a.fuel_type.value) == pair]
            self._pending.append(
                _Interval(pair, names[pair[0]], t0, snap.tick, stock[pair] - before[pair], sum(a.quantity for a in mine), tuple(sorted(a.id for a in mine)), max(a.actual_arrival_tick or t0 for a in mine))
            )

    def settle(self, served_rows: Iterable[tuple[str, str, int, float]]) -> None:
        """Judge the waiting arrivals. Rows are (station, fuel, tick, served litres)."""
        if not self._pending:
            return
        served = {(station, fuel, tick): litres for station, fuel, tick, litres in served_rows}
        waiting: list[_Interval] = []
        for i in self._pending:
            keys = [(*i.pair, t) for t in range(i.t0, i.t1)]
            if any(k not in served for k in keys):
                if self._tick - i.t1 <= MAX_SPAN_TICKS:
                    waiting.append(i)
                continue
            received = i.gained + sum(served[k] for k in keys)
            tolerance = max(LOSS_MIN_LITERS, LOSS_MIN_SHARE * i.sent)
            lost = i.sent - received
            # Receiving less than nothing or more than was sent means the figures do not belong together.
            if lost <= tolerance or received < -tolerance or self._tick - i.arrival_tick > KEEP_TICKS:
                continue
            ids = ", ".join(f"#{n}" for n in i.allocation_ids)
            fuel = i.pair[1].lower()
            key = f"loss:{i.pair[0]}:{i.pair[1]}:{i.t0}"
            summary = f"{lost:,.0f} L of {fuel} was lost: {'allocation' if len(i.allocation_ids) == 1 else 'allocations'} {ids} sent {i.sent:,.0f} L and the station received {max(0.0, received):,.0f} L."
            self._findings[key] = Finding(key, "lost_fuel", "lost", i.pair[0], i.station_name, i.pair[1], round(lost, 1), None, i.arrival_tick, i.arrival_tick, summary)
        self._pending = waiting

    def findings(self) -> tuple[Finding, ...]:
        """The findings open now: overdue deliveries first, then the newest."""
        return tuple(sorted(self._findings.values(), key=lambda f: (f.state != "overdue", -f.last_tick, f.id)))
