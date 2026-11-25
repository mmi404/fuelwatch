import asyncio

import pytest

from fuelwatch.client import SimulatorClient
from fuelwatch.forecast import DemandForecaster
from fuelwatch.models import Allocation, Event, FuelType
from fuelwatch.stockout import assess, project

from .conftest import make_world, transport_for


def run(coro):
    return asyncio.run(coro)


def base_snapshot(tick=0):
    snap = run(SimulatorClient("http://sim", transport=transport_for(make_world(tick))).read_world())
    # one station of an unknown profile, so the forecast is exactly what we train (flat 100 L per tick)
    st = snap.stations[0].model_copy(update={"demand_profile": "flat"})
    return snap.model_copy(update={"stations": (st,), "allocations": (), "events": ()})


def with_inventory(snap, diesel, cap=15000):
    st = snap.stations[0]
    st = st.model_copy(update={"inventory": {**st.inventory, FuelType.DIESEL: diesel}, "capacity": {**st.capacity, FuelType.DIESEL: cap}})
    return snap.model_copy(update={"stations": (st,)})


def flat_forecaster(snap, level=100.0, ticks=100):
    f = DemandForecaster()
    st = snap.stations[0]
    f.train([(st.id, fuel.value, t, level) for t in range(ticks) for fuel in st.capacity], [st], list(snap.regions), [], snap.tick_minutes)
    return f


def diesel(assessments):
    return next(a for a in assessments if a.fuel_type == "DIESEL")


# -- the projection ----------------------------------------------------------------------------


def test_project_serves_until_stock_is_short():
    assert project(500, 1000, {}, [100] * 10, 0, set()) == 5  # 5 ticks served, the 6th (tick 5) is short
    assert project(1000, 1000, {}, [100] * 10, 0, set()) is None  # exactly enough for 10 ticks


def test_project_adds_arrivals_before_demand_in_the_same_tick():
    # 100 L at 100 L per tick: the tank is empty after tick 0, and the 100 L arriving at tick 1 is served in tick 1
    assert project(100, 1000, {1: 100}, [100] * 2, 0, set()) is None
    assert project(100, 1000, {1: 100}, [100] * 3, 0, set()) == 2  # nothing left for tick 2
    assert project(100, 1000, {}, [100] * 3, 0, set()) == 1  # without the delivery tick 1 is already short


def test_project_loses_the_part_that_does_not_fit():
    # a 500 L tank with 450 in it: only 50 of a 400 L delivery is kept
    assert project(450, 500, {0: 400}, [100] * 10, 0, set()) == 5  # 500 stock lasts 5 ticks of 100


def test_project_ignores_demand_while_the_station_is_closed():
    assert project(100, 1000, {}, [60] * 6, 0, {1, 2, 3}) == 4  # tick 0 serves 60, ticks 1-3 closed, tick 4 is short


# -- the assessment -----------------------------------------------------------------------------


def test_a_comfortable_station_is_ok_and_has_no_stockout_in_the_horizon():
    snap = with_inventory(base_snapshot(), 14000)
    a = diesel(assess(snap, flat_forecaster(snap)))
    assert a.level == "OK" and a.hours_p50 is None and a.hours_p90 is None and a.risk < 0.01


def test_p90_runs_out_before_p50_and_hours_are_in_hours():
    snap = with_inventory(base_snapshot(), 3000)  # about 30 ticks of 100 L
    a = diesel(assess(snap, flat_forecaster(snap)))
    assert a.stockout_tick_p90 < a.stockout_tick_p50 <= 31
    assert a.hours_p50 == pytest.approx((a.stockout_tick_p50 - snap.tick) * 15 / 60)


@pytest.mark.parametrize("inv,level", [(1500, "CRITICAL"), (4500, "WARNING"), (8000, "WATCH"), (14000, "OK")])
def test_levels_follow_the_hours_to_stockout(inv, level):
    snap = with_inventory(base_snapshot(), inv)
    a = diesel(assess(snap, flat_forecaster(snap)))
    assert a.level == level, (inv, a.hours_p90)


def test_in_transit_fuel_postpones_the_stockout_and_a_pending_one_uses_the_route_time():
    snap = with_inventory(base_snapshot(), 1500)
    no_help = diesel(assess(snap, flat_forecaster(snap)))
    route = snap.routes[0]  # transit 2 ticks
    sent = Allocation(
        id=9, idempotency_key="k", source_depot_id="depot-gazipur", destination_station_id="station-mirpur", route_id=route.id,
        fuel_type="DIESEL", quantity=6000, created_tick=0, departure_tick=None, expected_arrival_tick=None, status="PENDING",
    )
    helped = diesel(assess(snap.model_copy(update={"allocations": (sent,)}), flat_forecaster(snap)))
    assert helped.inbound == 6000
    assert helped.stockout_tick_p50 is None or helped.stockout_tick_p50 > no_help.stockout_tick_p50 + 40
    in_transit = sent.model_copy(update={"status": "IN_TRANSIT", "expected_arrival_tick": 40})
    late = diesel(assess(snap.model_copy(update={"allocations": (in_transit,)}), flat_forecaster(snap)))
    assert late.stockout_tick_p50 == no_help.stockout_tick_p50  # it arrives after the station is already dry


def test_a_station_in_outage_does_not_run_out_while_closed():
    snap = with_inventory(base_snapshot(), 1500)
    outage = Event(id=1, type="station_outage", start_tick=0, end_tick=11, status="ACTIVE", parameters={"station_ids": ["station-mirpur"]})
    with_outage = snap.model_copy(update={"events": (outage,)})
    plain = diesel(assess(snap, flat_forecaster(snap)))
    closed = diesel(assess(with_outage, flat_forecaster(snap)))
    assert closed.stockout_tick_p50 == plain.stockout_tick_p50 + 12  # twelve ticks of no consumption


def test_a_scheduled_spike_brings_the_stockout_forward():
    snap = with_inventory(base_snapshot(), 6000)
    spike = Event(id=2, type="demand_spike", start_tick=5, end_tick=20, status="SCHEDULED", parameters={"multiplier": 3.0})
    calm = diesel(assess(snap, flat_forecaster(snap)))
    spiky = diesel(assess(snap.model_copy(update={"events": (spike,)}), flat_forecaster(snap)))
    assert spiky.stockout_tick_p50 < calm.stockout_tick_p50 - 15


def test_critical_threshold_grows_with_the_route_lead_time():
    snap = with_inventory(base_snapshot(), 2400)  # about 24 ticks = 6 h at 100 L per tick
    short = diesel(assess(snap, flat_forecaster(snap)))
    slow_route = snap.routes[0].model_copy(update={"transit_ticks": 28})  # lead 28 + 4 = 32 ticks = 8 h
    slow = diesel(assess(snap.model_copy(update={"routes": (slow_route,)}), flat_forecaster(snap)))
    assert short.level != "CRITICAL" and slow.level == "CRITICAL"


def test_no_forecast_means_unknown_not_a_guess():
    snap = base_snapshot()
    a = diesel(assess(snap, DemandForecaster()))
    assert a.level == "UNKNOWN" and a.confidence == "none" and a.hours_p90 is None


def test_cold_start_with_a_known_profile_uses_the_prior_and_says_so():
    snap = run(SimulatorClient("http://sim", transport=transport_for(make_world(0))).read_world())  # profile urban_high
    snap = snap.model_copy(update={"allocations": (), "events": ()})
    a = diesel(assess(snap, DemandForecaster()))
    assert a.confidence == "prior" and a.level != "UNKNOWN"


def test_ranking_puts_the_most_urgent_first_and_a_tank_with_no_stockout_last():
    snap = base_snapshot()
    st = snap.stations[0]
    st = st.model_copy(update={"inventory": {FuelType.DIESEL: 1200, FuelType.PETROL: 2500, FuelType.OCTANE: 9000}, "capacity": {FuelType.DIESEL: 15000, FuelType.PETROL: 14000, FuelType.OCTANE: 9000}})
    snap = snap.model_copy(update={"stations": (st,)})
    forecaster = flat_forecaster(snap, level=40.0)  # 40 L per tick: octane lasts 225 ticks, beyond the horizon
    ranked = assess(snap, forecaster)
    assert [a.fuel_type for a in ranked] == ["DIESEL", "PETROL", "OCTANE"]
    assert ranked[-1].hours_p90 is None
    hours = [a.hours_p90 for a in ranked[:-1]]
    assert hours == sorted(hours)
