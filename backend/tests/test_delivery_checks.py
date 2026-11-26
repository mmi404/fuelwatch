from fuelwatch.delivery_checks import KEEP_TICKS, DeliveryChecker
from fuelwatch.models import Allocation, Event, SupplyArrival

from .test_allocate import snapshot

BASE = snapshot()
STATION, DEPOT = BASE.stations[0], BASE.depots[0]
FUEL = next(iter(STATION.inventory))


def supply(planned, status="SCHEDULED", actual=None):
    return SupplyArrival(id="supply-1", depot_id=DEPOT.id, fuel_type=FUEL, quantity=12000, planned_tick=planned, actual_tick=actual, status=status)


def delay(start, ticks, **params):
    return Event(id=1, type="shipment_delay", start_tick=start, end_tick=start + 1, status="RESOLVED", parameters={"delay_ticks": ticks, **params})


def allocation(arrival, quantity=3000.0):
    return Allocation(id=7, idempotency_key="k", source_depot_id=DEPOT.id, destination_station_id=STATION.id, route_id="r", fuel_type=FUEL, quantity=quantity, created_tick=arrival - 2, actual_arrival_tick=arrival, status="ARRIVED")


def snap(tick, *, arrivals=(), events=(), allocations=(), stock=1000.0, consistent=True):
    station = STATION.model_copy(update={"inventory": {**STATION.inventory, FUEL: stock}})
    instance = BASE.instance.model_copy(update={"tick": tick})
    return BASE.model_copy(update={"instance": instance, "stations": (station,), "supply_arrivals": tuple(arrivals), "events": tuple(events), "allocations": tuple(allocations), "consistent": consistent})


def run(*snaps):
    checker = DeliveryChecker()
    for s in snaps:
        checker.observe(s)
    return checker


def test_overdue_then_arrived_late_then_closed():
    checker = run(snap(10, arrivals=[supply(12)]), snap(12, arrivals=[supply(12)]))
    assert checker.findings() == ()  # tick 12 has not been processed yet
    checker.observe(snap(14, arrivals=[supply(12)]))
    (found,) = checker.findings()
    assert (found.state, found.ticks_late) == ("overdue", 2)
    done = supply(12, "ARRIVED", 15)
    checker.observe(snap(16, arrivals=[done]))
    (found,) = checker.findings()
    assert (found.state, found.ticks_late) == ("arrived_late", 3)
    checker.observe(snap(15 + KEEP_TICKS, arrivals=[done]))
    assert len(checker.findings()) == 1
    checker.observe(snap(16 + KEEP_TICKS, arrivals=[done]))
    assert checker.findings() == ()


def test_postponed_without_an_event_is_late_but_an_announced_delay_explains_it():
    moved, done = supply(20, "DELAYED"), supply(20, "ARRIVED", 20)
    before = snap(5, arrivals=[supply(12)])
    assert run(before, snap(21, arrivals=[done])).findings()[0].ticks_late == 8
    explained = [delay(8, 8)]  # long resolved: the delay is permanent
    assert run(before, snap(18, arrivals=[moved], events=explained), snap(21, arrivals=[done], events=explained)).findings() == ()
    elsewhere = [delay(8, 8, depot_ids=["depot-other"])]
    assert run(before, snap(18, arrivals=[moved], events=elsewhere)).findings()[0].ticks_late == 6
    too_late = [delay(13, 8)]  # started after the delivery was due, so it cannot have moved it
    assert len(run(before, snap(18, arrivals=[moved], events=too_late)).findings()) == 1


def test_starting_mid_run_reports_no_history():
    old = supply(12, "ARRIVED", 30)
    assert run(snap(40, arrivals=[old], allocations=[allocation(20)]), snap(41, arrivals=[old], allocations=[allocation(20)])).findings() == ()


def lost_after(stock_after, served, *, consistent=True):
    checker = run(snap(10, stock=1000), snap(12, stock=stock_after, allocations=[allocation(11)], consistent=consistent))
    checker.settle((STATION.id, FUEL.value, 10 + i, litres) for i, litres in enumerate(served))
    return checker


def test_lost_fuel_by_conservation_of_stock():
    (found,) = lost_after(2500, [100, 200]).findings()  # gained 1,500 + served 300 = 1,800 of 3,000 sent
    assert (found.kind, found.litres, found.place_id) == ("lost_fuel", 1200.0, STATION.id)
    assert "#7" in found.summary


def test_lost_fuel_stays_quiet_when_it_cannot_tell():
    assert lost_after(3700, [100, 200]).findings() == ()  # all of it arrived
    assert lost_after(3660, [100, 200]).findings() == ()  # within the tolerance
    assert lost_after(2500, [100, 200], consistent=False).findings() == ()
    waiting = lost_after(2500, [100])  # the arrival tick's demand is not synced yet
    assert waiting.findings() == () and waiting.history_needed_from() == 10
    waiting.settle([(STATION.id, FUEL.value, 10, 100), (STATION.id, FUEL.value, 11, 200)])
    assert len(waiting.findings()) == 1 and waiting.history_needed_from() is None


def test_reset_forgets_everything():
    checker = lost_after(2500, [100, 200])
    checker.reset()
    assert checker.findings() == ()
    checker.observe(snap(3))
    assert checker.findings() == ()
