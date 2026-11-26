import hashlib
import random
import time

import httpx
from fastapi.testclient import TestClient

from fuelwatch.anomaly import MIN_HISTORY, AnomalyDetector
from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.explain import confidence
from fuelwatch.forecast import DAILY_LITERS, NOISE_PRIOR, DemandForecaster, hour_of_day, hour_shape
from fuelwatch.main import create_app
from fuelwatch.allocate import plan_shipments
from fuelwatch.models import Event, Region, StationStatus
from fuelwatch.stockout import assess

from .conftest import Sleeps
from .test_allocate import forecaster_for, snapshot
from .test_proposals import Rig
from .test_world import FakeSim

FUELS = ("DIESEL", "PETROL", "OCTANE")
REGIONS = [Region(id="region-dhaka", name="Dhaka", demand_factor=1.0)]


def base_station():
    return snapshot().stations[0].model_copy(update={"demand_profile": "urban_high"})


def demand(station, tick, seed=12345, factor=1.0):
    """One station's tick as the simulator's engine draws it: profile x hour x noise, seeded by hash."""
    rows = []
    for fuel in FUELS:
        noise = NOISE_PRIOR[station.demand_profile]
        key = f"{seed}:{tick}:{station.id}:demand:{fuel}".encode()
        jitter = 1 + random.Random(int.from_bytes(hashlib.sha256(key).digest()[:8], "big")).uniform(-noise, noise)
        litres = DAILY_LITERS[station.demand_profile][fuel] / 96 * hour_shape(station.demand_profile, hour_of_day(tick, 15)) * jitter * factor
        rows.append((station.id, fuel, tick, litres))
    return rows


class Replay:
    """The forecaster feeding the detector one tick at a time, as the live service does."""

    def __init__(self, seed=12345):
        self.seed, self.next = seed, 0
        self.forecaster, self.detector = DemandForecaster(), AnomalyDetector()
        self.forecaster.on_station_tick = self.detector.observe
        self.station = base_station()
        self.seen = []

    def run(self, ticks, factor=1.0, events=(), station=None):
        station = station or self.station
        found = ()
        for _ in range(ticks):
            self.forecaster.train(demand(station, self.next, self.seed, factor), [station], REGIONS, list(events), 15)
            self.next += 1
            found = self.detector.evaluate(snapshot(tick=self.next).model_copy(update={"stations": (station,), "events": tuple(events)}))
            self.seen += found
        return found


def spike(start, end, multiplier):
    return Event(id=7, type="demand_spike", start_tick=start, end_tick=end, status="ACTIVE", parameters={"multiplier": multiplier})


def test_ordinary_demand_is_never_flagged():
    for seed in (12345, 1, 2, 3, 4):
        replay = Replay(seed)
        replay.run(5 * 96)
        assert replay.seen == []


def test_an_unannounced_jump_is_unexplained_after_two_ticks():
    replay = Replay()
    replay.run(100)
    assert replay.run(1, factor=1.4) == ()  # one tick is not enough
    (a,) = replay.run(1, factor=1.4)
    assert a.kind == "unexplained" and a.direction == "above" and a.first_tick == 100 and a.last_tick == 101
    assert a.summary.endswith("above forecast, no known cause") and 0.25 < a.deviation < 0.55
    assert replay.run(10) == ()  # back to normal: it closes


def test_one_outlier_alone_never_flags_and_a_gap_does_not_join_two():
    replay = Replay()
    replay.run(100)
    replay.run(1, factor=2.0)
    assert replay.run(3) == ()
    replay.run(1, factor=2.0)
    replay.next += 5  # the history has a hole
    assert replay.run(1, factor=2.0) == ()


def test_a_collapse_in_demand_is_flagged_below():
    replay = Replay()
    replay.run(100)
    (a,) = replay.run(2, factor=0.0)
    assert a.direction == "below" and a.kind == "unexplained"


def test_an_announced_spike_is_explained_and_named():
    replay = Replay()
    events = [spike(100, 111, 1.7)]
    replay.run(100)
    (a,) = replay.run(3, factor=1.7, events=events)
    assert a.kind == "explained" and "demand spike x1.7, started tick 100" in a.summary
    replay.run(9, factor=1.7, events=events)
    assert replay.run(3, events=events) == ()


def test_demand_far_beyond_an_announced_spike_is_unexplained():
    replay = Replay()
    replay.run(100)
    (a,) = replay.run(3, factor=2.2, events=[spike(100, 111, 1.3)])
    assert a.kind == "unexplained" and "more than the demand spike x1.3" in a.summary


def test_the_stations_own_multiplier_explains_a_matching_jump():
    replay = Replay()
    replay.run(100)
    raised = replay.station.model_copy(update={"demand_multiplier": 1.5})
    (a,) = replay.run(2, factor=1.5, station=raised)
    assert a.kind == "explained" and "demand multiplier is 1.5" in a.summary


def test_cold_start_never_flags():
    replay = Replay()
    replay.run(MIN_HISTORY // 2)
    replay.run(MIN_HISTORY // 2, factor=3.0)
    assert replay.seen == []


def test_a_tiny_sigma_does_not_turn_a_tiny_gap_into_a_flag():
    detector, station = AnomalyDetector(), base_station()
    for t in range(40):
        detector.observe(station, t, [(100.0 if t < 37 else 108.0, 100.0, 1.0, 0.0)], [])
    assert detector.evaluate(snapshot(tick=40)) == ()
    detector.observe(station, 40, [(100.0, 0.0, 1.0, 0.0)], [])  # no forecast: nothing to compare with
    assert detector.evaluate(snapshot(tick=41)) == ()


def test_outage_stations_reset_and_old_history_report_nothing():
    replay = Replay()
    replay.run(100)
    assert replay.run(2, factor=1.6)
    closed = replay.station.model_copy(update={"status": StationStatus.OUTAGE})
    assert replay.detector.evaluate(snapshot(tick=replay.next).model_copy(update={"stations": (closed,)})) == ()
    assert replay.detector.evaluate(snapshot(tick=replay.next + 20)) == ()  # too old to describe the present
    outage = Event(id=8, type="station_outage", start_tick=replay.next, end_tick=replay.next + 3, status="ACTIVE", parameters={"station_ids": [replay.station.id]})
    assert replay.run(3, factor=0.0, events=[outage]) == ()
    replay.detector.reset()
    assert replay.detector.evaluate(snapshot(tick=replay.next)) == ()


def test_only_an_unexplained_anomaly_lowers_confidence():
    snap = snapshot()
    f = DemandForecaster()
    for s in snap.stations:
        for fuel in s.capacity:
            f.prime(s, fuel.value, snap.regions)
    assessment = assess(snap, f)[0]
    plain, _, _ = confidence(assessment, snap, {})
    lowered, _, reasons = confidence(assessment, snap, {}, anomaly="Demand 40% above forecast, no known cause")
    assert round(plain - lowered, 2) == 0.20 and any("no known cause" in r for r in reasons)


def test_a_new_proposal_for_an_anomalous_station_carries_the_lower_confidence():
    def proposal(unexplained):
        rig, snap = Rig(), snapshot(diesel_inv=1500)
        rig.world.observe(snap)
        f = forecaster_for(snap)
        assessments = assess(snap, f)
        rig.service.sync(snap, f, assessments, plan_shipments(snap, f, assessments), unexplained)
        return rig.service.recent()[0]

    station_id = snapshot().stations[0].id
    plain, lowered = proposal({}), proposal({station_id: "Demand 40% above forecast, no known cause"})
    assert round(plain["confidence"] - lowered["confidence"], 2) == 0.20
    assert any("no known cause" in r for r in lowered["explanation"]["confidence"]["reasons"])


def test_the_api_lists_open_anomalies_and_the_gauge_has_both_kinds():
    sim = FakeSim()
    sim.step(8)
    settings = Settings(poll_interval=0.01, ready_max_age=60, database_path=":memory:")
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    app = create_app(settings=settings, client=client)
    with TestClient(app) as api:
        end = time.time() + 3
        while time.time() < end and not api.get("/api/risks").json():
            time.sleep(0.02)
        assert api.get("/api/anomalies").json() == []
        text = api.get("/metrics").text
        assert 'fuelwatch_anomalies_open{kind="explained"} 0.0' in text and 'fuelwatch_anomalies_open{kind="unexplained"} 0.0' in text
        replay = Replay()
        replay.run(100)
        found = replay.run(2, factor=1.4)
        app.state.intel.detector.evaluate = lambda snap: found  # the next poll publishes what the detector found
        app.state.intel._detect(app.state.intel.snapshot)
        (row,) = api.get("/api/anomalies").json()
        assert row["kind"] == "unexplained" and row["station_id"] == "station-mirpur" and "no known cause" in row["summary"]
        assert 'fuelwatch_anomalies_open{kind="unexplained"} 1.0' in api.get("/metrics").text
