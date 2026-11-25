"""Rolling-horizon linear programme (design part 6.4, policy 2).

The greedy allocator decides one station at a time. This one decides every station and fuel together over
the next `horizon` ticks, so a depot's scarce stock or dispatch capacity goes where it prevents the most
unmet demand. Only the shipments of the first tick are returned; the rest of the plan is thrown away and
recomputed at the next snapshot.

All quantities inside the model are in thousands of litres, which keeps the matrix well scaled for HiGHS.
Nothing here talks to the simulator or the database: a snapshot, a forecaster and the assessments go in,
a `Plan` (the same type the greedy allocator returns) comes out. Any failure raises `LpFailed`, and the
caller falls back to the greedy allocator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_array

from .allocate import AllocatorConfig, Decision, Plan, Shipment, Shortfall, disrupted_route_ids, headroom_liters
from .forecast import DemandForecaster
from .models import EventType, Route, StationStatus
from .snapshot import WorldSnapshot
from .stockout import Assessment, increments, inbound_by_tick, outage_ticks

Z80 = 0.8416  # standard normal 80th percentile
KL = 1000.0  # litres per model unit


class LpFailed(Exception):
    """The programme could not be built or solved in time; the caller falls back."""


@dataclass(frozen=True)
class LpConfig:
    horizon: int = 48  # ticks planned ahead (12 simulated hours at 15-minute ticks)
    risk_z: float = Z80  # demand path quantile used for planning
    buffer_ticks: int = 24  # keep stock for this many ticks of demand at every tick (soft), the same cover as the greedy policy
    buffer_cap_fraction: float = 0.85  # a buffer is never asked to exceed this share of the tank
    time_limit: float = 1.0  # seconds before the solve is abandoned
    c_unmet: float = 1.0
    c_buffer: float = 0.2
    c_hold: float = 0.002
    c_ship: float = 0.01
    c_lead: float = 0.002  # per litre and per tick of transit
    c_waste: float = 0.05
    c_overflow: float = 5.0
    min_shipment: float = 1000.0
    cover_ticks: int = 24  # used only for the relative minimum shipment of slow-moving fuels


def _blocked_at(snapshot: WorldSnapshot, route: Route, offset: int, now_blocked: set[str]) -> bool:
    """True if the route cannot depart at tick T + offset (events cover that tick inclusively)."""
    if offset == 0:
        return route.id in now_blocked
    tick = snapshot.tick + offset
    return any(e.type is EventType.ROUTE_DISRUPTION and e.start_tick <= tick <= e.end_tick and route.id in (e.parameters.get("route_ids") or []) for e in snapshot.events)


def _supply_schedule(snapshot: WorldSnapshot) -> dict[tuple[str, str], dict[int, float]]:
    """Litres that will reach each depot and fuel, by tick, with scheduled delays and shortfalls applied.

    The simulator applies a shipment_delay or supply_shortfall to the supply rows when the event starts, so
    an event that has not started yet is applied here to the rows it will still find waiting.
    """
    rows = [{"depot": a.depot_id, "fuel": a.fuel_type.value, "tick": a.actual_tick if a.actual_tick is not None else a.planned_tick, "qty": a.quantity, "status": a.status} for a in snapshot.supply_arrivals if a.status != "ARRIVED"]
    for e in sorted((e for e in snapshot.events if e.status == "SCHEDULED"), key=lambda e: (e.start_tick, e.id)):
        p = e.parameters
        depots, fuels = set(p.get("depot_ids") or []), set(p.get("fuel_types") or [])
        for r in rows:
            if (depots and r["depot"] not in depots) or (fuels and r["fuel"] not in fuels) or r["tick"] < e.start_tick:
                continue
            if e.type is EventType.SHIPMENT_DELAY and r["status"] == "SCHEDULED":
                r["tick"] += int(p.get("delay_ticks", 2))
                r["status"] = "DELAYED"
            elif e.type is EventType.SUPPLY_SHORTFALL:
                r["qty"] *= float(p.get("factor", 0.5))
    out: dict[tuple[str, str], dict[int, float]] = {}
    for r in rows:
        key = (r["depot"], r["fuel"])
        tick = max(snapshot.tick, r["tick"])
        out.setdefault(key, {})[tick] = out.setdefault(key, {}).get(tick, 0.0) + r["qty"]
    return out


class _Vars:
    """Hands out column numbers and keeps their bounds and costs."""

    def __init__(self) -> None:
        self.lower: list[float] = []
        self.upper: list[float] = []
        self.cost: list[float] = []

    def add(self, lo: float, hi: float, cost: float) -> int:
        self.lower.append(lo)
        self.upper.append(hi)
        self.cost.append(cost)
        return len(self.lower) - 1


class _Rows:
    def __init__(self) -> None:
        self.r: list[int] = []
        self.c: list[int] = []
        self.v: list[float] = []
        self.rhs: list[float] = []

    def add(self, terms: list[tuple[int, float]], rhs: float) -> None:
        row = len(self.rhs)
        for col, val in terms:
            self.r.append(row)
            self.c.append(col)
            self.v.append(val)
        self.rhs.append(rhs)

    def matrix(self, n: int) -> coo_array | None:
        if not self.rhs:
            return None
        return coo_array((self.v, (self.r, self.c)), shape=(len(self.rhs), n)).tocsr()


def plan_lp(snapshot: WorldSnapshot, forecaster: DemandForecaster, assessments: list[Assessment], cfg: LpConfig | None = None) -> Plan:
    cfg = cfg or LpConfig()
    T, H, B = snapshot.tick, cfg.horizon, cfg.buffer_ticks
    tm = snapshot.tick_minutes
    stations = {s.id: s for s in snapshot.stations}
    depots = {d.id: d for d in snapshot.depots}
    by_pair = {(a.station_id, a.fuel_type): a for a in assessments}
    now_blocked = disrupted_route_ids(snapshot)
    supply = _supply_schedule(snapshot)

    dispatch_now = {d.id: float(d.dispatch_capacity_per_tick) for d in snapshot.depots}
    for a in snapshot.allocations:  # shipments created this tick count against this tick's capacity
        if a.created_tick == T and a.status.value != "CANCELLED" and a.source_depot_id in dispatch_now:
            dispatch_now[a.source_depot_id] = max(0.0, dispatch_now[a.source_depot_id] - a.quantity)

    var, eq, ub = _Vars(), _Rows(), _Rows()

    # ---- station and fuel pairs ------------------------------------------------------------------
    pairs: list[tuple] = []  # (station, fuel enum, demand path in model units, closed ticks)
    for station in snapshot.stations:
        closed = outage_ticks(station, snapshot, T, H)
        for fuel in station.capacity:
            cum = forecaster.cumulative(station, fuel.value, T, H + B, tm, snapshot.events)
            if cum is None:
                continue  # no forecast yet: not planned
            path = [d / KL for d in increments(cum, cfg.risk_z)]
            pairs.append((station, fuel, path, closed))

    if not pairs:
        return Plan(T, (), (), tuple(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "skipped_unknown", ("no forecast yet",)) for a in assessments if a.level == "UNKNOWN"))

    # ---- columns -----------------------------------------------------------------------------------
    I: dict[tuple[int, int], int] = {}
    U: dict[tuple[int, int], int] = {}
    W: dict[tuple[int, int], int] = {}
    Z: dict[tuple[int, int], int] = {}
    X: dict[tuple[str, str, int], int] = {}  # (route id, fuel, departure offset)
    Jv: dict[tuple[str, str, int], int] = {}
    G: dict[tuple[str, str, int], int] = {}

    arcs: list[tuple[Route, object]] = []  # (route, fuel enum) that carry fuel to a planned pair
    pair_index = {(s.id, f.value): i for i, (s, f, _, _) in enumerate(pairs)}
    for r in snapshot.routes:
        if r.source_depot_id not in depots or r.destination_station_id not in stations:
            continue
        for f in stations[r.destination_station_id].capacity:
            if (r.destination_station_id, f.value) in pair_index and f in depots[r.source_depot_id].capacity:
                arcs.append((r, f))

    for i, (station, fuel, path, closed) in enumerate(pairs):
        cap = float(station.capacity[fuel]) / KL
        for t in range(H):
            I[i, t] = var.add(0.0, cap, cfg.c_hold)
            d = path[t]
            U[i, t] = var.add(d if (T + t) in closed else 0.0, d, cfg.c_unmet)
            W[i, t] = var.add(0.0, math.inf, cfg.c_overflow)
            Z[i, t] = var.add(0.0, math.inf, cfg.c_buffer)

    depot_fuels = sorted({(r.source_depot_id, f.value) for r, f in arcs})
    for k, fv in depot_fuels:
        fuel = next(f for f in depots[k].capacity if f.value == fv)
        dcap = float(depots[k].capacity[fuel]) / KL
        inv = min(dcap, float(depots[k].inventory.get(fuel, 0.0)) / KL)
        Jv[k, fv, 0] = var.add(inv, inv, 0.0)
        for t in range(H):
            Jv[k, fv, t + 1] = var.add(0.0, dcap, 0.0)
            G[k, fv, t] = var.add(0.0, math.inf, cfg.c_waste)

    for r, f in arcs:
        station = stations[r.destination_station_id]
        closed_dest = next(c for s, _, _, c in pairs if s.id == station.id)
        for t in range(H - r.transit_ticks):  # a shipment that lands after the horizon cannot help
            blocked = _blocked_at(snapshot, r, t, now_blocked) or (station.status is StationStatus.OUTAGE if t == 0 else (T + t - 1) in closed_dest)
            X[r.id, f.value, t] = var.add(0.0, 0.0 if blocked else r.max_shipment / KL, cfg.c_ship + cfg.c_lead * r.transit_ticks)

    # ---- station balance, overflow and buffer ---------------------------------------------------------
    arrivals_into: dict[tuple[int, int], list[int]] = {}
    for r, f in arcs:
        i = pair_index[r.destination_station_id, f.value]
        for t in range(H - r.transit_ticks):
            arrivals_into.setdefault((i, t + r.transit_ticks), []).append(X[r.id, f.value, t])

    for i, (station, fuel, path, closed) in enumerate(pairs):
        cap = float(station.capacity[fuel]) / KL
        start = float(station.inventory.get(fuel, 0.0)) / KL
        known = {t - T: q / KL for t, q in inbound_by_tick(snapshot, station.id, fuel).items() if t - T < H}
        for t in range(H):
            shipped = [(c, 1.0) for c in arrivals_into.get((i, t), [])]
            k_t = known.get(t, 0.0)
            # I_t = I_{t-1} + known + shipped - overflow - (demand - unmet)
            terms = [(I[i, t], 1.0), *[(c, -v) for c, v in shipped], (W[i, t], 1.0), (U[i, t], -1.0)]
            rhs = k_t - path[t]
            if t == 0:
                rhs += start
            else:
                terms.append((I[i, t - 1], -1.0))
            eq.add(terms, rhs)
            # nothing may be lost at arrival: stock after arrivals fits in the tank
            terms = [*shipped, (W[i, t], -1.0)]
            rhs = cap - k_t
            if t == 0:
                rhs -= start
            else:
                terms.append((I[i, t - 1], 1.0))
            ub.add(terms, rhs)
            # soft buffer: stock after the tick covers the next `buffer_ticks` ticks of demand
            need = min(sum(path[t + 1 : t + 1 + B]), cfg.buffer_cap_fraction * cap)
            ub.add([(I[i, t], -1.0), (Z[i, t], -1.0)], -need)

    # ---- depot balance, depot stock, dispatch ---------------------------------------------------------
    for k, fv in depot_fuels:
        sup = supply.get((k, fv), {})
        for t in range(H):
            out_cols = [X[r.id, f.value, t] for r, f in arcs if r.source_depot_id == k and f.value == fv and (r.id, f.value, t) in X]
            arrived = sup.get(T + t, 0.0) / KL
            eq.add([(Jv[k, fv, t + 1], 1.0), (Jv[k, fv, t], -1.0), *[(c, 1.0) for c in out_cols], (G[k, fv, t], 1.0)], arrived)
            if out_cols:
                ub.add([*[(c, 1.0) for c in out_cols], (Jv[k, fv, t], -1.0)], 0.0)
    for k in depots:
        for t in range(H):
            cols = [X[r.id, f.value, t] for r, f in arcs if r.source_depot_id == k and (r.id, f.value, t) in X]
            if cols:
                left = (dispatch_now[k] if t == 0 else float(depots[k].dispatch_capacity_per_tick)) / KL
                ub.add([(c, 1.0) for c in cols], left)

    n = len(var.lower)
    c = np.array(var.cost)
    bounds = list(zip(var.lower, var.upper, strict=True))
    a_eq, a_ub = eq.matrix(n), ub.matrix(n)
    try:
        res = linprog(c, A_ub=a_ub, b_ub=np.array(ub.rhs), A_eq=a_eq, b_eq=np.array(eq.rhs), bounds=bounds, method="highs", options={"time_limit": cfg.time_limit})
    except Exception as exc:  # noqa: BLE001 - any solver failure means "fall back"
        raise LpFailed(f"solver error: {exc!r}") from exc
    if res.status != 0 or res.x is None:
        raise LpFailed(f"solver status {res.status}: {res.message}")
    x = res.x

    return _commit(snapshot, forecaster, assessments, cfg, pairs, arcs, X, U, x, stations, depots, by_pair, now_blocked, dispatch_now)


def _commit(snapshot, forecaster, assessments, cfg, pairs, arcs, X, U, x, stations, depots, by_pair, now_blocked, dispatch_now) -> Plan:
    """Turn the first tick of the solution into whole-litre shipments that satisfy the simulator's checks."""
    T = snapshot.tick
    stock = {(d.id, f): float(q) for d in snapshot.depots for f, q in d.inventory.items()}
    dispatch_left = dict(dispatch_now)
    shipments: list[Shipment] = []
    shortfalls: list[Shortfall] = []
    decisions: list[Decision] = []
    planned_unmet: dict[tuple[str, str], float] = {}
    for i, (station, fuel, _path, _closed) in enumerate(pairs):
        planned_unmet[station.id, fuel.value] = sum(max(0.0, x[U[i, t]]) for t in range(cfg.horizon)) * KL
    arc_by_pair: dict[tuple[str, str], list[tuple[Route, object]]] = {}
    for r, f in arcs:
        arc_by_pair.setdefault((r.destination_station_id, f.value), []).append((r, f))
    base = AllocatorConfig(min_shipment=cfg.min_shipment, cover_ticks=cfg.cover_ticks)

    for a in assessments:
        key = (a.station_id, a.fuel_type)
        station = stations.get(a.station_id)
        if station is None:
            continue
        if a.level == "UNKNOWN":
            decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "skipped_unknown", ("no forecast yet",)))
            continue
        if a.level == "OK":
            decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "skipped_ok"))
            continue
        if station.status is StationStatus.OUTAGE:
            decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "station_outage", ("the simulator rejects shipments to a station in outage",)))
            continue
        fuel = next(f for f in station.capacity if f.value == a.fuel_type)
        routes_in = [(r, f) for r, f in arc_by_pair.get(key, [])]
        usable = [r for r, _ in routes_in if r.id not in now_blocked]
        notes = [f"route {r.id} is disrupted at tick {T}" for r, _ in routes_in if r.id in now_blocked]
        if not usable:
            shortfalls.append(Shortfall(station.id, a.fuel_type, 0.0, "no_usable_route"))
            decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, 0, 0, "no_route", tuple(notes)))
            continue

        arrivals = dict(inbound_by_tick(snapshot, station.id, fuel))
        closed = outage_ticks(station, snapshot, T, 96)
        wanted = sum(x[X[r.id, f.value, 0]] for r, f in routes_in if (r.id, f.value, 0) in X) * KL
        first = round(float(wanted), 1)
        sent, lines = 0, 0
        reasons: list[str] = []
        for r, f in sorted(routes_in, key=lambda rf: (rf[0].transit_ticks, rf[0].id)):
            col = X.get((r.id, f.value, 0))
            if col is None or r.id in now_blocked:
                continue
            lp_qty = x[col] * KL
            if lp_qty < 1.0:
                continue
            head = headroom_liters(snapshot, forecaster, station, fuel, r, arrivals, closed)
            depot_avail = stock.get((r.source_depot_id, fuel), 0.0)
            left = dispatch_left[r.source_depot_id]
            limits = {"need": lp_qty, "route_max": r.max_shipment, "depot_stock": depot_avail, "dispatch_capacity": left, "headroom": head}
            binding = min(limits, key=limits.get)
            qty = math.floor(limits[binding] + 1e-6)
            cover = forecaster.cumulative(station, fuel.value, T + r.transit_ticks, cfg.cover_ticks, snapshot.tick_minutes, snapshot.events)
            floor = min(cfg.min_shipment, max(1.0, 0.5 * (cover[-1].mean + 0.6745 * cover[-1].sd))) if cover else cfg.min_shipment
            if qty < floor:
                if binding in ("depot_stock", "dispatch_capacity") and lp_qty >= floor:
                    reasons.append(binding)
                continue
            if lines >= base.max_lines_per_pair:
                break
            shipments.append(Shipment(r.source_depot_id, station.id, r.id, a.fuel_type, qty, T + r.transit_ticks, binding))
            stock[(r.source_depot_id, fuel)] = depot_avail - qty
            dispatch_left[r.source_depot_id] = left - qty
            arrivals[T + r.transit_ticks] = arrivals.get(T + r.transit_ticks, 0.0) + qty
            sent += qty
            lines += 1
            if binding in ("route_max", "depot_stock", "dispatch_capacity"):
                reasons.append(binding)
        unmet = float(planned_unmet.get(key, 0.0))
        if unmet > 500.0 and (not sent or reasons):
            shortfalls.append(Shortfall(station.id, a.fuel_type, round(unmet, 1), reasons[0] if reasons else "lead_time"))
        outcome = "shipped" if sent and not reasons else "partial" if sent or reasons else "covered"
        decisions.append(Decision(a.station_id, a.fuel_type, a.level, a.hours_p90, first, float(sent), outcome, (*notes, f"planned jointly over {cfg.horizon} ticks at the P80 demand path")))
    return Plan(T, tuple(shipments), tuple(shortfalls), tuple(decisions), policy="lp")
