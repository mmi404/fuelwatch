"""Rules and the cover-based greedy allocator (design part 6.4, policy 1).

A pure function: a snapshot, a forecaster and the stockout assessments go in; a plan of
shipments, with the constraint that limited each one, comes out. Nothing here talks to the
simulator or the database, so the same code runs live and in the replay benchmark.

For each station and fuel that is not OK, most urgent first, it tops the tank up so that, after
the shipment arrives, there is enough stock to cover the next `cover_ticks` of demand at the P75
level. It never sends more than fits (headroom is taken at the projected arrival, net of fuel
already on the road, using the low-demand path so it cannot overflow), and it respects every rule
the simulator checks at POST time plus the one it checks at departure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .forecast import DemandForecaster
from .models import EventType, Route, RouteStatus, Station, StationStatus
from .snapshot import WorldSnapshot
from .stockout import Assessment, increments, inbound_by_tick, inventory_at, outage_ticks

Z75 = 0.6745  # standard normal 75th percentile


@dataclass(frozen=True)
class AllocatorConfig:
    cover_ticks: int = 24  # stock to hold after arrival, in ticks of P75 demand (6 h at 15-minute ticks)
    min_shipment: float = 1000.0  # smaller needs are not worth a shipment
    depot_reserve: float = 0.0  # litres a depot keeps back per fuel
    max_lines_per_pair: int = 6  # a safety bound on shipments for one station and fuel in one tick


@dataclass(frozen=True)
class Shipment:
    depot_id: str
    station_id: str
    route_id: str
    fuel_type: str
    quantity: int  # whole litres, as posted
    arrival_tick: int
    binding: str  # what limited the size: need, route_max, depot_stock, dispatch_capacity, headroom


@dataclass(frozen=True)
class Shortfall:
    station_id: str
    fuel_type: str
    unmet_liters: float
    reason: str  # no_usable_route, depot_stock, dispatch_capacity


@dataclass(frozen=True)
class Decision:
    """Why a station and fuel got what it got, for the explanation card."""

    station_id: str
    fuel_type: str
    level: str
    hours_p90: float | None
    need_liters: float  # the first computed need
    sent_liters: float
    outcome: str  # covered, shipped, partial, skipped_ok, skipped_unknown, station_outage, no_route
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    tick: int
    shipments: tuple[Shipment, ...]
    shortfalls: tuple[Shortfall, ...]
    decisions: tuple[Decision, ...] = field(default=())

    @property
    def total_liters(self) -> int:
        return sum(s.quantity for s in self.shipments)


def disrupted_route_ids(snapshot: WorldSnapshot) -> set[str]:
    """Routes the simulator would fail at departure this tick.

    A POST at tick T departs in the processing of tick T, after that tick's events start. So a route
    is unusable if it is DISRUPTED now or any route_disruption covering tick T names it, even one that
    is still SCHEDULED (a disruption starting exactly at T). The end tick is inclusive.
    """
    blocked = {r.id for r in snapshot.routes if r.status is RouteStatus.DISRUPTED}
    for e in snapshot.events:
        if e.type is EventType.ROUTE_DISRUPTION and e.start_tick <= snapshot.tick <= e.end_tick:
            blocked.update(e.parameters.get("route_ids") or [])
    return blocked


def _need(snapshot: WorldSnapshot, forecaster: DemandForecaster, station: Station, fuel, route: Route, arrivals: dict[int, float], closed: set[int], cfg: AllocatorConfig) -> tuple[float, float, float, float]:
    """(need, headroom, uncapped_need, smallest_worth_sending) in litres for shipping on `route`."""
    T, tm = snapshot.tick, snapshot.tick_minutes
    arrival = T + route.transit_ticks
    inv = float(station.inventory.get(fuel, 0.0))
    cap = float(station.capacity.get(fuel, 0.0))
    lead = forecaster.cumulative(station, fuel.value, T, max(1, route.transit_ticks), tm, snapshot.events)
    window = forecaster.cumulative(station, fuel.value, arrival, cfg.cover_ticks, tm, snapshot.events)
    if lead is None or window is None:
        return 0.0, 0.0, 0.0, cfg.min_shipment
    lead = lead[: route.transit_ticks] if route.transit_ticks else []
    low_stock = inventory_at(inv, cap, arrivals, increments(lead, Z75), T, arrival, closed) if lead else inventory_at(inv, cap, arrivals, [], T, arrival, closed)
    high_stock = inventory_at(inv, cap, arrivals, increments(lead, -Z75), T, arrival, closed) if lead else low_stock
    demand = window[-1].mean + Z75 * window[-1].sd
    later = sum(q for t, q in arrivals.items() if arrival < t < arrival + cfg.cover_ticks)
    uncapped = max(0.0, demand - low_stock - later)
    headroom = max(0.0, cap - high_stock)
    # A shipment smaller than `min_shipment` is not worth sending, unless the whole cover is smaller
    # than that (a slow-moving fuel): then half the cover is the smallest worth sending, otherwise
    # such a pair would never be topped up until its tank was already empty.
    floor = min(cfg.min_shipment, max(1.0, 0.5 * demand))
    return min(uncapped, headroom), headroom, uncapped, floor


def plan_shipments(snapshot: WorldSnapshot, forecaster: DemandForecaster, assessments: list[Assessment], cfg: AllocatorConfig | None = None) -> Plan:
    cfg = cfg or AllocatorConfig()
    T = snapshot.tick
    stations = {s.id: s for s in snapshot.stations}
    depots = {d.id: d for d in snapshot.depots}
    stock = {(d.id, fuel): float(q) for d in snapshot.depots for fuel, q in d.inventory.items()}
    dispatch_left = {d.id: float(d.dispatch_capacity_per_tick) for d in snapshot.depots}
    # Dispatch capacity is per tick and counts allocations created in this same tick (`[live]`).
    for a in snapshot.allocations:
        if a.created_tick == T and a.status.value != "CANCELLED" and a.source_depot_id in dispatch_left:
            dispatch_left[a.source_depot_id] = max(0.0, dispatch_left[a.source_depot_id] - a.quantity)
    blocked = disrupted_route_ids(snapshot)
    routes_into: dict[str, list[Route]] = {}
    for r in snapshot.routes:
        routes_into.setdefault(r.destination_station_id, []).append(r)

    shipments: list[Shipment] = []
    shortfalls: list[Shortfall] = []
    decisions: list[Decision] = []

    for a in assessments:  # already most urgent first
        station = stations.get(a.station_id)
        if station is None:
            continue
        fuel = next(f for f in station.capacity if f.value == a.fuel_type)
        if a.level == "UNKNOWN":
            decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "skipped_unknown", ("no forecast yet",)))
            continue
        if a.level == "OK":
            decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "skipped_ok"))
            continue
        if station.status is StationStatus.OUTAGE:
            decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "station_outage", ("the simulator rejects shipments to a station in outage",)))
            continue

        notes: list[str] = []
        usable = [r for r in routes_into.get(station.id, []) if r.id not in blocked and r.source_depot_id in depots]
        for r in routes_into.get(station.id, []):
            if r.id in blocked:
                notes.append(f"route {r.id} is disrupted at tick {T}")
        if not usable:
            shortfalls.append(Shortfall(station.id, a.fuel_type, 0.0, "no_usable_route"))
            decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "no_route", tuple(notes)))
            continue

        closed = outage_ticks(station, snapshot, T, 96)
        arrivals = dict(inbound_by_tick(snapshot, station.id, fuel))
        first_need: float | None = None
        sent = 0
        pair_lines = 0
        last_reason = "covered"
        while pair_lines < cfg.max_lines_per_pair:
            # Best route first: shortest transit, then the depot holding more of this fuel.
            options = sorted(usable, key=lambda r: (r.transit_ticks, -stock.get((r.source_depot_id, fuel), 0.0), r.id))
            placed = False
            limiting = "covered"
            for route in options:
                need, headroom, uncapped, floor = _need(snapshot, forecaster, station, fuel, route, arrivals, closed, cfg)
                if first_need is None:
                    first_need = need
                if need < floor:
                    limiting = "headroom" if uncapped >= floor and headroom < floor else "covered"
                    last_reason = limiting
                    break  # a longer route would not need more
                depot_avail = stock.get((route.source_depot_id, fuel), 0.0) - cfg.depot_reserve
                left = dispatch_left[route.source_depot_id]
                limits = {"need": need, "route_max": route.max_shipment, "depot_stock": depot_avail, "dispatch_capacity": left}
                binding = min(limits, key=limits.get)
                if need == headroom and headroom < uncapped and binding == "need":
                    binding = "headroom"
                if limits["need" if binding == "headroom" else binding] < floor:
                    last_reason = binding
                    continue  # this route's depot cannot serve it; try the next route
                qty = math.floor(limits["need" if binding == "headroom" else binding])
                shipments.append(Shipment(route.source_depot_id, station.id, route.id, a.fuel_type, qty, T + route.transit_ticks, binding))
                stock[(route.source_depot_id, fuel)] = stock.get((route.source_depot_id, fuel), 0.0) - qty
                dispatch_left[route.source_depot_id] = left - qty
                arrivals[T + route.transit_ticks] = arrivals.get(T + route.transit_ticks, 0.0) + qty
                sent += qty
                pair_lines += 1
                placed = True
                break
            if not placed:
                if last_reason not in ("covered", "headroom"):
                    # Something other than "enough" stopped us: record what is still needed.
                    need = _need(snapshot, forecaster, station, fuel, min(usable, key=lambda r: r.transit_ticks), arrivals, closed, cfg)[0]
                    shortfalls.append(Shortfall(station.id, a.fuel_type, round(need, 1), last_reason))
                break

        outcome = "shipped" if sent and last_reason in ("covered", "headroom") else "partial" if sent else "covered" if last_reason in ("covered", "headroom") else "partial"
        decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, round(first_need or 0.0, 1), float(sent), outcome, tuple(notes)))

    return Plan(T, tuple(shipments), tuple(shortfalls), tuple(decisions))
