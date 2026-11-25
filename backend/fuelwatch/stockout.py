"""Stockout estimator (design part 6.3).

For every station and fuel, project the inventory forward tick by tick in the order the
simulator uses inside a tick (arrivals first, then demand), once along the mean demand
path and once along the P90 path, and report when stock first runs short. A snapshot and a
forecaster go in; a ranked list of assessments comes out. Pure function: no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .forecast import Cumulative, DemandForecaster
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


def _increments(cumulative: list[Cumulative], quantile_90: bool) -> list[float]:
    values = [c.p90 if quantile_90 else c.mean for c in cumulative]
    out, previous = [], 0.0
    for v in values:
        out.append(max(0.0, v - previous))
        previous = max(previous, v)
    return out


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
    route_ticks = {r.id: r.transit_ticks for r in snapshot.routes}
    day_ticks = 1440 // tm
    out: list[Assessment] = []

    for station in snapshot.stations:
        closed = _outage_ticks(station, snapshot, T, horizon)
        routes = routes_by_station.get(station.id, [])
        usable = [r.transit_ticks for r in routes if r.status.value == "AVAILABLE"] or [r.transit_ticks for r in routes]
        best_route = min(usable) if usable else None
        critical_below = max(4.0, ((best_route or 0) + 4) * tm / 60.0) if best_route is not None else 4.0

        for fuel in station.capacity:
            f = fuel.value
            forecaster.prime(station, f, snapshot.regions)
            inventory = float(station.inventory.get(fuel, 0.0))
            capacity = float(station.capacity.get(fuel, 0.0))
            arrivals: dict[int, float] = {}
            for a in snapshot.allocations:
                if a.status in _ON_THE_ROAD and a.destination_station_id == station.id and a.fuel_type == fuel:
                    eta = a.expected_arrival_tick if a.expected_arrival_tick is not None else a.created_tick + route_ticks.get(a.route_id, 0)
                    arrivals[max(T, eta)] = arrivals.get(max(T, eta), 0.0) + a.quantity
            inbound = sum(arrivals.values())
            observations = forecaster.observations(station.id, f)
            confidence = "prior" if observations == 0 else "low" if observations < day_ticks else "normal"

            cumulative = forecaster.cumulative(station, f, T, horizon, tm, snapshot.events)
            if cumulative is None:
                out.append(Assessment(station.id, f, T, inventory, capacity, inbound, "UNKNOWN", None, None, None, None, None, best_route, "none", observations))
                continue

            def stockout(q90: bool) -> int | None:
                path = _increments(cumulative, q90)
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


def _outage_ticks(station: Station, snapshot: WorldSnapshot, start: int, horizon: int) -> set[int]:
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
