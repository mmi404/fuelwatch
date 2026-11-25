import pytest

from fuelwatch import lp as lp_module
from fuelwatch.allocate import plan_rules, plan_shipments
from fuelwatch.explain import confidence
from fuelwatch.lp import LpConfig, LpFailed, _supply_schedule, plan_lp
from fuelwatch.models import Event, FuelType, SupplyArrival
from fuelwatch.planner import PlanningFailed, plan_with_fallback
from fuelwatch.stockout import assess

from .test_allocate import depot, diesel, forecaster_for, route, snapshot


def lp_plan(snap, level=100.0, cfg=None):
    f = forecaster_for(snap, level)
    return plan_lp(snap, f, assess(snap, f), cfg)


def event(eid, kind, start, end, status="SCHEDULED", **parameters):
    return Event.model_validate({"id": eid, "type": kind, "start_tick": start, "end_tick": end, "status": status, "parameters": parameters})


def supply(sid, depot_id, fuel, qty, planned, status="SCHEDULED", actual=None):
    return SupplyArrival.model_validate({"id": sid, "depot_id": depot_id, "fuel_type": fuel, "quantity": qty, "planned_tick": planned, "actual_tick": actual, "status": status})


def test_a_comfortable_station_gets_nothing_and_the_plan_names_its_policy():
    p = lp_plan(snapshot(diesel_inv=14000))
    assert diesel(p.shipments) == [] and p.policy == "lp" and p.fallback_from is None


def test_a_critical_station_gets_whole_litres_within_every_limit():
    snap = snapshot(diesel_inv=1500)
    p = lp_plan(snap)
    (s,) = diesel(p.shipments)
    assert isinstance(s.quantity, int) and s.quantity >= 1000
    assert s.quantity <= 7000 and s.arrival_tick == snap.tick + 2
    assert s.quantity <= 15000 - 1500  # never more than fits in the tank


def test_a_disrupted_route_is_not_used():
    routes = [route("r-a", "depot-a", tau=2), route("r-b", "depot-b", tau=4)]
    blocked = event(1, "route_disruption", 0, 10, status="ACTIVE", route_ids=["r-a"])
    snap = snapshot(diesel_inv=1500, depots=[depot("depot-a"), depot("depot-b")], routes=routes, events=[blocked])
    p = lp_plan(snap)
    assert {s.route_id for s in diesel(p.shipments)} == {"r-b"}


def test_no_usable_route_is_reported_as_a_shortfall():
    blocked = event(1, "route_disruption", 0, 10, status="ACTIVE", route_ids=["r-a"])
    snap = snapshot(diesel_inv=1500, depots=[depot("depot-a")], routes=[route("r-a", "depot-a")], events=[blocked])
    p = lp_plan(snap)
    assert diesel(p.shipments) == []
    assert any(f.reason == "no_usable_route" for f in p.shortfalls)


def test_a_station_in_outage_gets_nothing_now():
    p = lp_plan(snapshot(diesel_inv=500, status="OUTAGE"))
    assert diesel(p.shipments) == []
    assert next(d for d in p.decisions if d.fuel_type == "DIESEL").outcome == "station_outage"


def test_depot_stock_and_dispatch_capacity_are_respected():
    snap = snapshot(diesel_inv=800, depots=[depot("depot-a", diesel=2500, dispatch=1800.0)], routes=[route("r-a", "depot-a")])
    p = lp_plan(snap)
    total = sum(s.quantity for s in diesel(p.shipments))
    assert 0 < total <= 1800
    assert all(s.binding in ("dispatch_capacity", "need", "headroom") for s in diesel(p.shipments))


def test_shipments_already_created_this_tick_use_up_dispatch_capacity():
    from fuelwatch.models import Allocation

    already = Allocation.model_validate({"id": 1, "idempotency_key": "k", "source_depot_id": "depot-a", "destination_station_id": "station-other", "route_id": "r-x", "fuel_type": "PETROL", "quantity": 1500.0, "created_tick": 0, "status": "PENDING"})
    snap = snapshot(diesel_inv=800, depots=[depot("depot-a", dispatch=2000.0)], routes=[route("r-a", "depot-a")], allocations=[already])
    p = lp_plan(snap)
    assert sum(s.quantity for s in diesel(p.shipments)) <= 500


def test_fuel_already_on_the_road_is_counted():
    from fuelwatch.models import Allocation

    on_road = Allocation.model_validate({"id": 1, "idempotency_key": "k", "source_depot_id": "depot-a", "destination_station_id": "station-mirpur", "route_id": "r-a", "fuel_type": "DIESEL", "quantity": 12000.0, "created_tick": 0, "departure_tick": 0, "expected_arrival_tick": 2, "status": "IN_TRANSIT"})
    snap = snapshot(diesel_inv=1500, depots=[depot("depot-a")], routes=[route("r-a", "depot-a")], allocations=[on_road])
    assert diesel(lp_plan(snap).shipments) == []


def test_scarce_stock_goes_to_the_station_that_runs_out_first():
    from fuelwatch.models import Station

    snap = snapshot(diesel_inv=1500, depots=[depot("depot-a", diesel=3000)], routes=[route("r-a", "depot-a")])
    first = snap.stations[0]
    other = Station.model_validate({**first.model_dump(mode="json"), "id": "station-tongi", "name": "Tongi", "inventory": {**{k.value: v for k, v in first.inventory.items()}, "DIESEL": 9000}})
    routes = (*snap.routes, route("r-b", "depot-a").model_copy(update={"id": "r-b", "destination_station_id": "station-tongi"}))
    snap = snap.model_copy(update={"stations": (first, other), "routes": routes})
    f = forecaster_for(snap, 100.0)
    f.train([(other.id, fuel.value, t, 100.0) for t in range(100) for fuel in other.capacity], [other], list(snap.regions), [], snap.tick_minutes)
    p = plan_lp(snap, f, assess(snap, f))
    sent = {}
    for s in diesel(p.shipments):
        sent[s.station_id] = sent.get(s.station_id, 0) + s.quantity
    assert sum(sent.values()) <= 3000
    assert sent.get("station-mirpur", 0) >= sent.get("station-tongi", 0)


def test_supply_schedule_applies_events_that_have_not_started():
    rows = [supply("a", "depot-a", "DIESEL", 10000.0, 20), supply("b", "depot-a", "PETROL", 8000.0, 22), supply("c", "depot-a", "DIESEL", 5000.0, 3, status="ARRIVED", actual=3)]
    delay = event(1, "shipment_delay", 5, 6, depot_ids=["depot-a"], fuel_types=["DIESEL"], delay_ticks=8)
    cut = event(2, "supply_shortfall", 6, 7, factor=0.5)
    snap = snapshot().model_copy(update={"supply_arrivals": tuple(rows), "events": (delay, cut)})
    got = _supply_schedule(snap)
    assert got[("depot-a", "DIESEL")] == {28: 5000.0}  # delayed 8 ticks, then halved; the arrived row is gone
    assert got[("depot-a", "PETROL")] == {22: 4000.0}


def test_started_events_are_not_applied_twice():
    rows = [supply("a", "depot-a", "DIESEL", 5000.0, 28, status="DELAYED")]
    started = event(1, "supply_shortfall", 0, 9, status="ACTIVE", factor=0.5)
    snap = snapshot().model_copy(update={"supply_arrivals": tuple(rows), "events": (started,)})
    assert _supply_schedule(snap)[("depot-a", "DIESEL")] == {28: 5000.0}


def test_a_solver_failure_becomes_lp_failed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no solver")

    monkeypatch.setattr(lp_module, "linprog", boom)
    with pytest.raises(LpFailed):
        lp_plan(snapshot(diesel_inv=1500))


def test_a_solve_that_does_not_finish_becomes_lp_failed():
    with pytest.raises(LpFailed):
        lp_plan(snapshot(diesel_inv=1500), cfg=LpConfig(time_limit=0.0))


# -- the fallback chain and the simplest policy ------------------------------------------------


class Obs:
    def __init__(self):
        self.calls = []

    def fallback(self, component, old, new):
        self.calls.append((component, old, new))


def planner_inputs():
    snap = snapshot(diesel_inv=1500)
    f = forecaster_for(snap)
    return snap, f, assess(snap, f)


def test_lp_failure_falls_back_to_greedy_and_says_so():
    snap, f, a = planner_inputs()
    obs = Obs()

    def broken(*args):
        raise LpFailed("infeasible")

    plan, seconds = plan_with_fallback(snap, f, a, "lp", obs, steps={"lp": broken, "greedy": plan_shipments, "rules": plan_rules})
    assert plan.policy == "greedy" and plan.fallback_from == "lp" and seconds >= 0
    assert obs.calls == [("allocator", "lp", "greedy")]
    assert diesel(plan.shipments)


def test_two_failures_end_on_the_rules_and_three_end_with_no_plan():
    snap, f, a = planner_inputs()
    obs = Obs()

    def broken(*args):
        raise RuntimeError("x")

    plan, _ = plan_with_fallback(snap, f, a, "lp", obs, steps={"lp": broken, "greedy": broken, "rules": plan_rules})
    assert plan.policy == "rules" and plan.fallback_from == "lp"
    assert obs.calls == [("allocator", "lp", "greedy"), ("allocator", "greedy", "rules")]
    with pytest.raises(PlanningFailed):
        plan_with_fallback(snap, f, a, "lp", Obs(), steps={"lp": broken, "greedy": broken, "rules": broken})


def test_a_working_policy_does_not_fall_back_and_unknown_policy_is_rejected():
    snap, f, a = planner_inputs()
    obs = Obs()
    plan, _ = plan_with_fallback(snap, f, a, "lp", obs)
    assert plan.policy == "lp" and obs.calls == []
    assert plan_with_fallback(snap, f, a, "greedy")[0].policy == "greedy"
    with pytest.raises(ValueError):
        plan_with_fallback(snap, f, a, "magic")


def test_rules_top_up_only_critical_pairs_with_what_fits():
    snap, f, a = planner_inputs()
    p = plan_rules(snap, f, a)
    assert p.policy == "rules"
    (s,) = p.shipments
    assert s.fuel_type == "DIESEL" and 500 <= s.quantity <= 7000
    assert plan_rules(*[snapshot(diesel_inv=14000), forecaster_for(snapshot(diesel_inv=14000)), []]).shipments == ()


def test_a_fallback_costs_confidence():
    snap, f, a = planner_inputs()
    base = confidence(a[0], snap, f.accuracy())
    fell = confidence(a[0], snap, f.accuracy(), "lp")
    assert round(base[0] - fell[0], 2) == 0.10 and any("lp allocator failed" in r for r in fell[2])


def test_headroom_never_exceeds_the_room_in_the_tank_now():
    from fuelwatch.allocate import headroom_liters
    from fuelwatch.stockout import inbound_by_tick, outage_ticks

    snap = snapshot(diesel_inv=9000, cap=15000)
    f = forecaster_for(snap, level=1000.0)
    station = snap.stations[0]
    fuel = next(k for k in station.capacity if k.value == "DIESEL")
    r = snap.routes[0]
    room = headroom_liters(snap, f, station, fuel, r, dict(inbound_by_tick(snap, station.id, fuel)), outage_ticks(station, snap, snap.tick, 96))
    assert room <= 15000 - 9000  # the simulator checks quantity against the room now, not at arrival


def test_the_api_serves_the_policy_that_produced_each_proposal():
    from .test_executor import _first_diesel, make_api

    _, app = make_api("t")
    with app as api:
        assert _first_diesel(api)["policy"] == "lp"
