"""Stockout estimator (design part 6.3).

For every station and fuel, project the inventory forward tick by tick in the order the
simulator uses inside a tick (arrivals first, then demand), once along the mean demand
path and once along the P90 path, and report when stock first runs short. A snapshot and a
forecaster go in; a ranked list of assessments comes out. Pure function: no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .forecast import Z90, Cumulative, DemandForecaster
from .models import AllocationStatus, EventType, Station
from .snapshot import WorldSnapshot

HORIZON_TICKS = 96
RISK_WINDOW_TICKS = 24
LEVELS = ("CRITICAL", "WARNING", "WATCH", "OK", "UNKNOWN")
_ON_THE_ROAD = (AllocationStatus.PENDING, AllocationStatus.IN_TRANSIT)


@dataclass(frozen=True)
class Assessment:
    station_id: str
    fuel_type: str
    tick: int
    inventory: float
    capacity: float
    inbound: float  # litres on the road to this station and fuel
    level: str  # CRITICAL, WARNING, WATCH, OK, or UNKNOWN when there is no forecast
    hours_p50: float | None  # None: no stockout within the horizon
    hours_p90: float | None
    stockout_tick_p50: int | None
    stockout_tick_p90: int | None
    risk: float | None  # probability that demand over the risk window exceeds stock plus inbound
    best_route_ticks: int | None
    confidence: str  # prior (no data yet), low (under one day learned) or normal
    observations: int


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def increments(cumulative: list[Cumulative], z: float) -> list[float]:
    """Per-tick demand along the path where cumulative demand is `mean + z x sd` (z = 0 is the mean path)."""
    out, previous = [], 0.0
    for c in cumulative:
        v = c.mean + z * c.sd
        out.append(max(0.0, v - previous))
        previous = max(previous, v)
    return out


def inbound_by_tick(snapshot: WorldSnapshot, station_id: str, fuel) -> dict[int, float]:
    """Litres already on the road to a station and fuel, by arrival tick.

    An IN_TRANSIT allocation arrives at its expected_arrival_tick; a PENDING one has not departed
    yet and arrives at created_tick + the route's transit time.
    """
    route_ticks = {r.id: r.transit_ticks for r in snapshot.routes}
    arrivals: dict[int, float] = {}
    for a in snapshot.allocations:
        if a.status in _ON_THE_ROAD and a.destination_station_id == station_id and a.fuel_type == fuel:
            eta = a.expected_arrival_tick if a.expected_arrival_tick is not None else a.created_tick + route_ticks.get(a.route_id, 0)
            tick = max(snapshot.tick, eta)
            arrivals[tick] = arrivals.get(tick, 0.0) + a.quantity
    return arrivals


def inventory_at(inventory: float, capacity: float, arrivals: dict[int, float], demand: list[float], start_tick: int, until_tick: int, closed: set[int]) -> float:
    """Stock in the tank at `until_tick` after that tick's arrivals and before its demand.

    Same order as `project`: each tick adds arrivals (the part that does not fit is lost), then serves demand.
    """
    stock = inventory
    for i, d in enumerate(demand):
        t = start_tick + i
        if t >= until_tick:
            break
        stock += min(max(0.0, capacity - stock), arrivals.get(t, 0.0))
        if t not in closed:
            stock -= min(stock, d)
    if until_tick >= start_tick:
        stock += min(max(0.0, capacity - stock), arrivals.get(until_tick, 0.0))
    return stock


def project(inventory: float, capacity: float, arrivals: dict[int, float], demand: list[float], start_tick: int, closed: set[int]) -> int | None:
    """First tick at which demand is not fully served, or None within the horizon.

    Mirrors the simulator's order inside a tick: arrivals are added first (what does not fit in the
    tank is lost), then demand is served from stock. A closed (outage) tick serves and consumes nothing.
    """
    stock = inventory
    for i, d in enumerate(demand):
        t = start_tick + i
        stock += min(max(0.0, capacity - stock), arrivals.get(t, 0.0))
        if t in closed:
            continue
        if d > stock + 1e-9:
            return t
        stock -= d
    return None


def _level(hours_p90: float | None, critical_below: float) -> str:
    if hours_p90 is None:
        return "OK"
    if hours_p90 < critical_below:
        return "CRITICAL"
    if hours_p90 < 12.0:
        return "WARNING"
    if hours_p90 < 24.0:
        return "WATCH"
    return "OK"


def assess(snapshot: WorldSnapshot, forecaster: DemandForecaster, horizon: int = HORIZON_TICKS) -> list[Assessment]:
    """Assess every station and fuel, most urgent first."""
    T, tm = snapshot.tick, snapshot.tick_minutes
    routes_by_station: dict[str, list] = {}
    for r in snapshot.routes:
        routes_by_station.setdefault(r.destination_station_id, []).append(r)
    day_ticks = 1440 // tm
    out: list[Assessment] = []

    for station in snapshot.stations:
        closed = outage_ticks(station, snapshot, T, horizon)
        routes = routes_by_station.get(station.id, [])
        usable = [r.transit_ticks for r in routes if r.status.value == "AVAILABLE"] or [r.transit_ticks for r in routes]
        best_route = min(usable) if usable else None
        critical_below = max(4.0, ((best_route or 0) + 4) * tm / 60.0) if best_route is not None else 4.0

        for fuel in station.capacity:
            f = fuel.value
            forecaster.prime(station, f, snapshot.regions)
            inventory = float(station.inventory.get(fuel, 0.0))
            capacity = float(station.capacity.get(fuel, 0.0))
            arrivals = inbound_by_tick(snapshot, station.id, fuel)
            inbound = sum(arrivals.values())
            observations = forecaster.observations(station.id, f)
            confidence = "prior" if observations == 0 else "low" if observations < day_ticks else "normal"

            cumulative = forecaster.cumulative(station, f, T, horizon, tm, snapshot.events)
            if cumulative is None:
                out.append(Assessment(station.id, f, T, inventory, capacity, inbound, "UNKNOWN", None, None, None, None, None, best_route, "none", observations))
                continue

            def stockout(q90: bool) -> int | None:
                path = increments(cumulative, Z90 if q90 else 0.0)
                return project(inventory, capacity, arrivals, path, T, closed)

            t50, t90 = stockout(False), stockout(True)
            h50 = None if t50 is None else (t50 - T) * tm / 60.0
            h90 = None if t90 is None else (t90 - T) * tm / 60.0

            w = min(RISK_WINDOW_TICKS, horizon)
            window = cumulative[w - 1]
            available = inventory + sum(q for t, q in arrivals.items() if t < T + w)
            risk = (1.0 if window.mean > available else 0.0) if window.sd <= 0 else 1.0 - _phi((available - window.mean) / window.sd)

            out.append(Assessment(station.id, f, T, inventory, capacity, inbound, _level(h90, critical_below), h50, h90, t50, t90, risk, best_route, confidence, observations))

    out.sort(key=lambda a: (a.hours_p90 is None, a.hours_p90 if a.hours_p90 is not None else 0.0, a.station_id, a.fuel_type))
    return out


@dataclass(frozen=True)
class WhatIf:
    """The outlook for one station and fuel if extra fuel arrives at the given ticks."""

    level: str
    hours_p50: float | None
    hours_p90: float | None
    risk: float | None
    unmet_p90: float  # litres of demand not served over UNMET_WINDOW_TICKS on the P90 path


UNMET_WINDOW_TICKS = 48


def unmet_liters(inventory: float, capacity: float, arrivals: dict[int, float], demand: list[float], start_tick: int, closed: set[int]) -> float:
    """Litres of demand that cannot be served along `demand`, same tick order as `project`."""
    stock, unmet = inventory, 0.0
    for i, d in enumerate(demand):
        t = start_tick + i
        stock += min(max(0.0, capacity - stock), arrivals.get(t, 0.0))
        if t in closed:
            unmet += d
            continue
        served = min(stock, d)
        unmet += d - served
        stock -= served
    return unmet


def what_if(snapshot: WorldSnapshot, forecaster: DemandForecaster, station_id: str, fuel_type: str, extra: dict[int, float] | None = None, horizon: int = HORIZON_TICKS) -> WhatIf | None:
    """Re-run the estimator for one pair with `extra` litres arriving at the given ticks.

    The same projection `assess` uses, so "do nothing", "this shipment" and "this shipment one tick
    later" are all measured with one yardstick. None when the pair is unknown or has no forecast.
    """
    T, tm = snapshot.tick, snapshot.tick_minutes
    station = next((s for s in snapshot.stations if s.id == station_id), None)
    fuel = next((f for f in (station.capacity if station else {}) if f.value == fuel_type), None)
    if station is None or fuel is None:
        return None
    cumulative = forecaster.cumulative(station, fuel_type, T, horizon, tm, snapshot.events)
    if cumulative is None:
        return None
    routes = [r for r in snapshot.routes if r.destination_station_id == station_id]
    usable = [r.transit_ticks for r in routes if r.status.value == "AVAILABLE"] or [r.transit_ticks for r in routes]
    best_route = min(usable) if usable else None
    critical_below = max(4.0, ((best_route or 0) + 4) * tm / 60.0) if best_route is not None else 4.0
    inventory, capacity = float(station.inventory.get(fuel, 0.0)), float(station.capacity.get(fuel, 0.0))
    arrivals = dict(inbound_by_tick(snapshot, station_id, fuel))
    for t, q in (extra or {}).items():
        arrivals[t] = arrivals.get(t, 0.0) + q
    closed = outage_ticks(station, snapshot, T, horizon)
    t50 = project(inventory, capacity, arrivals, increments(cumulative, 0.0), T, closed)
    p90_path = increments(cumulative, Z90)
    t90 = project(inventory, capacity, arrivals, p90_path, T, closed)
    h50 = None if t50 is None else (t50 - T) * tm / 60.0
    h90 = None if t90 is None else (t90 - T) * tm / 60.0
    w = min(RISK_WINDOW_TICKS, horizon)
    window = cumulative[w - 1]
    available = inventory + sum(q for t, q in arrivals.items() if t < T + w)
    risk = (1.0 if window.mean > available else 0.0) if window.sd <= 0 else 1.0 - _phi((available - window.mean) / window.sd)
    unmet = unmet_liters(inventory, capacity, arrivals, p90_path[:UNMET_WINDOW_TICKS], T, closed)
    return WhatIf(_level(h90, critical_below), h50, h90, risk, unmet)


def outage_ticks(station: Station, snapshot: WorldSnapshot, start: int, horizon: int) -> set[int]:
    """Ticks in the horizon during which the station serves nothing (a station_outage covering it, inclusive)."""
    closed: set[int] = set()
    for e in snapshot.events:
        if e.type is not EventType.STATION_OUTAGE:
            continue
        stations = e.parameters.get("station_ids")
        if stations and station.id not in stations:
            continue
        closed.update(range(max(start, e.start_tick), min(start + horizon, e.end_tick + 1)))
    if station.status.value == "OUTAGE":
        # Currently closed: the event list says when it ends; if no event explains it, assume it lasts the first tick only.
        closed.add(start)
    return closed
