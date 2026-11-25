import asyncio

import pytest

from fuelwatch.allocate import AllocatorConfig, disrupted_route_ids, plan_shipments
from fuelwatch.client import SimulatorClient
from fuelwatch.forecast import DemandForecaster
from fuelwatch.models import Allocation, Depot, Event, FuelType, Route, StationStatus
from fuelwatch.stockout import assess

from .conftest import make_world, transport_for


def run(coro):
    return asyncio.run(coro)


def depot(did, diesel=60000, dispatch=12000.0):
    return Depot.model_validate({
        "id": did, "name": did, "region_id": "region-dhaka", "status": "OPEN", "dispatch_capacity_per_tick": dispatch,
        "capacity": {"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000}, "inventory": {"DIESEL": diesel, "PETROL": 45000, "OCTANE": 26000},
    })


def route(rid, did, tau=2, max_shipment=7000.0, status="AVAILABLE"):
    return Route.model_validate({"id": rid, "source_depot_id": did, "destination_station_id": "station-mirpur", "transit_ticks": tau, "max_shipment": max_shipment, "status": status})


def snapshot(diesel_inv=1500, cap=15000, depots=None, routes=None, allocations=(), events=(), tick=0, status="OPEN"):
    snap = run(SimulatorClient("http://sim", transport=transport_for(make_world(tick))).read_world())
    st = snap.stations[0].model_copy(update={
        "demand_profile": "flat", "status": StationStatus(status),
        "inventory": {**snap.stations[0].inventory, FuelType.DIESEL: diesel_inv}, "capacity": {**snap.stations[0].capacity, FuelType.DIESEL: cap},
    })
    return snap.model_copy(update={
        "stations": (st,), "allocations": tuple(allocations), "events": tuple(events),
        "depots": tuple(depots or snap.depots), "routes": tuple(routes or snap.routes),
    })


def forecaster_for(snap, level=100.0, ticks=100):
    f = DemandForecaster()
    st = snap.stations[0]
    f.train([(st.id, fuel.value, t, level) for t in range(ticks) for fuel in st.capacity], [st], list(snap.regions), [], snap.tick_minutes)
    return f


def plan(snap, level=100.0, cfg=None):
    f = forecaster_for(snap, level)
    return plan_shipments(snap, f, assess(snap, f), cfg)


def diesel(shipments):
    return [s for s in shipments if s.fuel_type == "DIESEL"]


def test_a_comfortable_station_gets_nothing():
    p = plan(snapshot(diesel_inv=14000))
    assert diesel(p.shipments) == []
    assert next(d for d in p.decisions if d.fuel_type == "DIESEL").outcome == "skipped_ok"


def test_a_low_station_is_topped_up_to_cover_and_never_past_the_tank():
    snap = snapshot(diesel_inv=1500)
    p = plan(snap)
    (s,) = diesel(p.shipments)
    assert s.route_id == "route-gazipur-mirpur" and s.arrival_tick == 2 and s.binding == "need"
    # about 2,420 L of P75 demand over the 24 cover ticks, less the roughly 1,295 L left at arrival (1,500 - 2 x 100)
    assert 1000 < s.quantity < 1250
    assert snap.stations[0].inventory[FuelType.DIESEL] + s.quantity <= 15000


def test_what_is_already_on_the_road_counts_so_nothing_is_sent_twice():
    snap = snapshot(diesel_inv=1500)
    first = diesel(plan(snap).shipments)[0]
    sent = Allocation(id=5, idempotency_key="k", source_depot_id="depot-gazipur", destination_station_id="station-mirpur", route_id="route-gazipur-mirpur",
                      fuel_type="DIESEL", quantity=first.quantity, created_tick=0, status="PENDING")
    again = plan(snap.model_copy(update={"allocations": (sent,)}))
    assert diesel(again.shipments) == []


def test_headroom_is_taken_at_arrival_net_of_inbound():
    # a 5,000 L tank with 1,000 in it and 3,500 already on the road: at most about 500 more fits
    sent = Allocation(id=5, idempotency_key="k", source_depot_id="depot-gazipur", destination_station_id="station-mirpur", route_id="route-gazipur-mirpur",
                      fuel_type="DIESEL", quantity=3500, created_tick=0, status="PENDING")
    snap = snapshot(diesel_inv=1000, cap=5000, allocations=(sent,))
    p = plan(snap, level=200.0, cfg=AllocatorConfig(min_shipment=100))
    shipped = sum(s.quantity for s in diesel(p.shipments))
    assert 1000 + 3500 + shipped - 2 * 200 * 0.7 <= 5000 + 1  # never more than the tank holds after two ticks of demand at the low path


def test_large_need_is_split_by_route_maximum():
    snap = snapshot(diesel_inv=500)
    p = plan(snap, level=900.0, cfg=AllocatorConfig(min_shipment=1000))  # cover of 24 ticks x 900 is more than one 7,000 L load
    lines = diesel(p.shipments)
    assert len(lines) >= 2 and all(s.quantity <= 7000 for s in lines)
    assert lines[0].binding == "route_max"


def test_dispatch_capacity_binds_and_the_rest_goes_to_another_depot():
    snap = snapshot(
        diesel_inv=500,
        depots=[depot("depot-a", dispatch=4000.0), depot("depot-b", dispatch=12000.0)],
        routes=[route("r-a", "depot-a", tau=2), route("r-b", "depot-b", tau=3)],
    )
    p = plan(snap, level=900.0, cfg=AllocatorConfig(min_shipment=1000))
    per_depot = {}
    for s in diesel(p.shipments):
        per_depot[s.depot_id] = per_depot.get(s.depot_id, 0) + s.quantity
    assert per_depot["depot-a"] <= 4000 and per_depot.get("depot-b", 0) > 0
    assert diesel(p.shipments)[0].depot_id == "depot-a"  # shortest transit first


def test_same_tick_allocations_already_posted_use_up_dispatch_capacity():
    posted = Allocation(id=7, idempotency_key="x", source_depot_id="depot-gazipur", destination_station_id="station-other", route_id="route-gazipur-mirpur",
                        fuel_type="PETROL", quantity=11500, created_tick=0, status="PENDING")
    p = plan(snapshot(diesel_inv=500, allocations=(posted,)), level=900.0)
    assert sum(s.quantity for s in diesel(p.shipments)) <= 500  # 12,000 dispatch less 11,500 already used


def test_ties_go_to_the_depot_with_more_stock():
    snap = snapshot(diesel_inv=1500, depots=[depot("depot-a", diesel=20000), depot("depot-b", diesel=50000)], routes=[route("r-a", "depot-a"), route("r-b", "depot-b")])
    (s,) = diesel(plan(snap).shipments)
    assert s.depot_id == "depot-b"


def test_a_depot_with_too_little_stock_is_skipped_and_the_shortfall_is_recorded():
    snap = snapshot(diesel_inv=500, depots=[depot("depot-a", diesel=300)], routes=[route("r-a", "depot-a")])
    p = plan(snap, level=500.0)
    assert diesel(p.shipments) == []
    assert any(sf.reason == "depot_stock" and sf.fuel_type == "DIESEL" for sf in p.shortfalls)


def test_a_disrupted_route_is_avoided_including_one_scheduled_to_start_now():
    snap = snapshot(diesel_inv=1500, depots=[depot("depot-a"), depot("depot-b")], routes=[route("r-a", "depot-a", tau=1), route("r-b", "depot-b", tau=4)])
    scheduled_now = Event(id=1, type="route_disruption", start_tick=0, end_tick=5, status="SCHEDULED", parameters={"route_ids": ["r-a"]})
    s = snap.model_copy(update={"events": (scheduled_now,)})
    assert disrupted_route_ids(s) == {"r-a"}
    (ship,) = diesel(plan(s).shipments)
    assert ship.route_id == "r-b"


def test_a_disruption_that_has_not_started_or_has_ended_does_not_block():
    snap = snapshot(tick=10)
    later = Event(id=1, type="route_disruption", start_tick=11, end_tick=20, status="SCHEDULED", parameters={"route_ids": ["route-gazipur-mirpur"]})
    over = Event(id=2, type="route_disruption", start_tick=2, end_tick=9, status="RESOLVED", parameters={"route_ids": ["route-gazipur-mirpur"]})
    assert disrupted_route_ids(snap.model_copy(update={"events": (later, over)})) == set()
    active_end = Event(id=3, type="route_disruption", start_tick=2, end_tick=10, status="ACTIVE", parameters={"route_ids": ["route-gazipur-mirpur"]})
    assert disrupted_route_ids(snap.model_copy(update={"events": (active_end,)})) == {"route-gazipur-mirpur"}  # end tick inclusive


def test_with_every_route_blocked_nothing_is_sent_and_it_says_why():
    snap = snapshot(diesel_inv=1500, routes=[route("route-gazipur-mirpur", "depot-gazipur", status="DISRUPTED")])
    p = plan(snap)
    assert diesel(p.shipments) == []
    assert any(sf.reason == "no_usable_route" for sf in p.shortfalls)
    assert any("disrupted" in n for d in p.decisions for n in d.notes)


def test_a_station_in_outage_is_not_sent_fuel():
    p = plan(snapshot(diesel_inv=1500, status="OUTAGE"))
    assert p.shipments == ()
    assert next(d for d in p.decisions if d.fuel_type == "DIESEL").outcome == "station_outage"


def test_a_slow_moving_fuel_is_still_topped_up_even_when_its_whole_cover_is_below_the_minimum():
    # 30 L per tick: 24 ticks is about 720 L, under the 1,000 L minimum, and the tank is nearly dry
    p = plan(snapshot(diesel_inv=100), level=30.0, cfg=AllocatorConfig(min_shipment=1000))
    (s,) = diesel(p.shipments)
    assert 300 < s.quantity < 1000


def test_most_urgent_gets_the_scarce_stock_first():
    # two fuels at the same station competing for one depot's dispatch capacity of 3,000 L per tick
    snap = snapshot(diesel_inv=500, depots=[depot("depot-a", dispatch=3000.0)], routes=[route("r-a", "depot-a")])
    st = snap.stations[0].model_copy(update={"inventory": {FuelType.DIESEL: 500, FuelType.PETROL: 3000, FuelType.OCTANE: 9000}})
    snap = snap.model_copy(update={"stations": (st,)})
    p = plan(snap, level=100.0, cfg=AllocatorConfig(min_shipment=500))
    by_fuel = {}
    for s in p.shipments:
        by_fuel[s.fuel_type] = by_fuel.get(s.fuel_type, 0) + s.quantity
    assert by_fuel.get("DIESEL", 0) >= by_fuel.get("PETROL", 0)  # diesel is the more urgent and is served first


def test_without_a_forecast_nothing_is_guessed():
    snap = snapshot(diesel_inv=100)
    f = DemandForecaster()
    p = plan_shipments(snap, f, assess(snap, f))
    assert p.shipments == () and all(d.outcome == "skipped_unknown" for d in p.decisions)


def test_quantities_are_whole_litres_and_every_shipment_respects_every_limit():
    snap = snapshot(diesel_inv=800, depots=[depot("depot-a", diesel=9000, dispatch=5000.0)], routes=[route("r-a", "depot-a", max_shipment=2500.0)])
    p = plan(snap, level=400.0, cfg=AllocatorConfig(min_shipment=500))
    lines = diesel(p.shipments)
    assert lines and all(isinstance(s.quantity, int) and s.quantity <= 2500 for s in lines)
    assert sum(s.quantity for s in lines) <= 5000 and sum(s.quantity for s in lines) <= 9000
