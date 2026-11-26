"""Explanation card and confidence for a proposal (design 6.6 and 6.7).

Every number on the card comes from the same estimator the allocator used, so the effect of the
action, of doing nothing and of waiting one tick are measured with one yardstick.
"""

from __future__ import annotations

from .allocate import Decision, Shipment, Shortfall, disrupted_route_ids
from .forecast import DemandForecaster, spike_multiplier
from .models import EventType, Station
from .snapshot import WorldSnapshot
from .stockout import Assessment, WhatIf, what_if

COVERAGE_BAND = (0.72, 0.85)
COVERAGE_MIN_SCORED = 200  # forecasts scored before the coverage figure is trusted
CONFIDENCE_HIGH, CONFIDENCE_MEDIUM = 0.8, 0.5
_ARRIVAL_WINDOW = 48  # ticks of scheduled supply shown on the card


def confidence(assessment: Assessment, snapshot: WorldSnapshot, accuracy: dict[str, object], fallback_from: str | None = None, anomaly: str | None = None) -> tuple[float, str, list[str]]:
    """Start at 1.0 and subtract the reductions of design 6.7 that apply."""
    score, reasons = 1.0, []
    if assessment.confidence != "normal":
        score -= 0.30
        reasons.append("less than one day of demand history for this station and fuel")
    if anomaly:
        score -= 0.20
        reasons.append(f"unexplained demand anomaly at this station: {anomaly}")
    if snapshot.stale or not snapshot.consistent:
        score -= 0.20
        reasons.append("the snapshot is stale or was read while the tick moved")
    coverage, scored = accuracy.get("coverage_80"), int(accuracy.get("forecasts_scored") or 0)
    if isinstance(coverage, float) and scored >= COVERAGE_MIN_SCORED and not (COVERAGE_BAND[0] <= coverage <= COVERAGE_BAND[1]):
        score -= 0.10
        reasons.append(f"recent forecast coverage is {coverage:.0%}, outside {COVERAGE_BAND[0]:.0%} to {COVERAGE_BAND[1]:.0%}")
    if fallback_from:
        score -= 0.10
        reasons.append(f"the {fallback_from} allocator failed and a simpler policy produced this plan")
    score = round(max(0.0, score), 2)
    level = "High" if score >= CONFIDENCE_HIGH else "Medium" if score >= CONFIDENCE_MEDIUM else "Low"
    if not reasons:
        reasons.append("enough demand history, a fresh consistent snapshot and a calibrated forecast")
    return score, level, reasons


def _outlook(w: WhatIf | None) -> dict[str, object] | None:
    if w is None:
        return None
    return {"level": w.level, "hours_p50": w.hours_p50, "hours_p90": w.hours_p90, "risk": w.risk, "unmet_p90_liters": round(w.unmet_p90, 1)}


def _relevant_events(snapshot: WorldSnapshot, station: Station, route_ids: set[str], until: int) -> list[dict[str, object]]:
    out = []
    for e in snapshot.events:
        if e.end_tick < snapshot.tick or e.start_tick > until:
            continue
        p = e.parameters
        if e.type is EventType.DEMAND_SPIKE and spike_multiplier(station, e.start_tick, [e]) == 1.0:
            continue  # a spike that does not apply to this station
        if e.type is EventType.STATION_OUTAGE and p.get("station_ids") and station.id not in p["station_ids"]:
            continue
        if e.type is EventType.ROUTE_DISRUPTION and not (set(p.get("route_ids") or []) & route_ids):
            continue
        out.append({"id": e.id, "type": e.type.value, "start_tick": e.start_tick, "end_tick": e.end_tick, "status": e.status, "parameters": p})
    return out


def build_explanation(
    snapshot: WorldSnapshot,
    forecaster: DemandForecaster,
    assessment: Assessment,
    decision: Decision | None,
    shipments: list[Shipment],
    shortfalls: list[Shortfall],
    accuracy: dict[str, object],
    fallback_from: str | None = None,
    anomaly: str | None = None,
) -> tuple[dict[str, object], float, float | None, float | None]:
    """The card, plus the confidence score and the risk before and after."""
    T = snapshot.tick
    station = next(s for s in snapshot.stations if s.id == assessment.station_id)
    fuel = assessment.fuel_type
    routes = {r.id: r for r in snapshot.routes}
    extra: dict[int, float] = {}
    for s in shipments:
        extra[s.arrival_tick] = extra.get(s.arrival_tick, 0.0) + s.quantity
    delayed = {t + 1: q for t, q in extra.items()}

    before = what_if(snapshot, forecaster, station.id, fuel)
    after = what_if(snapshot, forecaster, station.id, fuel, extra)
    wait = what_if(snapshot, forecaster, station.id, fuel, delayed)

    alternatives: list[dict[str, object]] = [
        {"option": "do_nothing", "outlook": _outlook(before)},
        {"option": "wait_one_tick", "outlook": _outlook(wait), "note": "the same shipments, one tick later"},
    ]
    blocked = disrupted_route_ids(snapshot)
    used = {s.route_id for s in shipments}
    fuel_enum = next(f for f in station.capacity if f.value == fuel)
    depot_stock = {d.id: float(d.inventory.get(fuel_enum, 0.0)) for d in snapshot.depots}
    others = sorted(
        (r for r in snapshot.routes if r.destination_station_id == station.id and r.id not in used and r.id not in blocked and depot_stock.get(r.source_depot_id, 0.0) > 0),
        key=lambda r: (r.transit_ticks, r.id),
    )
    if others and shipments:
        r = others[0]
        qty = min(float(sum(s.quantity for s in shipments)), r.max_shipment, depot_stock[r.source_depot_id])
        alt = what_if(snapshot, forecaster, station.id, fuel, {T + r.transit_ticks: qty})
        alternatives.append({"option": "other_route", "route_id": r.id, "depot_id": r.source_depot_id, "liters": int(qty), "arrival_tick": T + r.transit_ticks, "outlook": _outlook(alt)})

    score, level, reasons = confidence(assessment, snapshot, accuracy, fallback_from, anomaly)
    cumulative = forecaster.cumulative(station, fuel, T, max(1, max((s.arrival_tick - T for s in shipments), default=1)), snapshot.tick_minutes, snapshot.events)
    to_arrival = cumulative[-1] if cumulative else None
    last_arrival = max((s.arrival_tick for s in shipments), default=T)
    supply = [
        {"depot_id": a.depot_id, "liters": a.quantity, "planned_tick": a.planned_tick, "actual_tick": a.actual_tick, "status": a.status}
        for a in snapshot.supply_arrivals
        if a.fuel_type.value == fuel and a.depot_id in {s.depot_id for s in shipments} and a.status != "ARRIVED" and T <= (a.actual_tick or a.planned_tick) <= T + _ARRIVAL_WINDOW
    ]

    def hours(h: float | None) -> str:
        return "no stockout within 24 h" if h is None else f"{h:.1f} h"

    total = int(sum(s.quantity for s in shipments))
    card = {
        "headline": {
            "station_id": station.id,
            "station_name": station.name,
            "fuel_type": fuel,
            "level": assessment.level,
            "hours_p50": assessment.hours_p50,
            "hours_p90": assessment.hours_p90,
            "text": f"{station.name} {fuel.lower()}: {assessment.level}, stock lasts {hours(assessment.hours_p90)} at high demand (P90) and {hours(assessment.hours_p50)} at expected demand",
        },
        "action": {
            "total_liters": total,
            "lines": [
                {"depot_id": s.depot_id, "route_id": s.route_id, "fuel_type": s.fuel_type, "liters": s.quantity, "transit_ticks": routes[s.route_id].transit_ticks, "arrival_tick": s.arrival_tick, "binding": s.binding}
                for s in shipments
            ],
        },
        "effect": {
            "risk_before": before.risk if before else assessment.risk,
            "risk_after": after.risk if after else None,
            "hours_p90_before": before.hours_p90 if before else assessment.hours_p90,
            "hours_p90_after": after.hours_p90 if after else None,
            "level_after": after.level if after else None,
            "unmet_avoided_p90_liters": round(max(0.0, (before.unmet_p90 if before else 0.0) - (after.unmet_p90 if after else 0.0)), 1),
            "window_ticks": 48,
        },
        "signals": {
            "inventory": assessment.inventory,
            "capacity": assessment.capacity,
            "inbound_liters": assessment.inbound,
            "expected_demand_until_arrival": round(to_arrival.mean, 1) if to_arrival else None,
            "p90_demand_until_arrival": round(to_arrival.p90, 1) if to_arrival else None,
            "demand_observations": assessment.observations,
            "events": _relevant_events(snapshot, station, {s.route_id for s in shipments} | {r.id for r in snapshot.routes if r.destination_station_id == station.id}, last_arrival),
            "scheduled_supply": supply,
        },
        "binding_constraints": sorted({s.binding for s in shipments if s.binding != "need"}),
        "shortfalls": [{"unmet_liters": f.unmet_liters, "reason": f.reason} for f in shortfalls],
        "notes": list(decision.notes) if decision else [],
        "alternatives": alternatives,
        "confidence": {"score": score, "level": level, "reasons": reasons},
    }
    return card, score, (before.risk if before else assessment.risk), (after.risk if after else None)
