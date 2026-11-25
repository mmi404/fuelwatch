import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.db import Database
from fuelwatch.intel import Intelligence
from fuelwatch.main import create_app
from fuelwatch.poller import Poller
from fuelwatch.snapshot import SnapshotStore
from fuelwatch.world import WorldTracker

from .conftest import Sleeps
from .test_world import FakeSim


def run(coro):
    return asyncio.run(coro)


def build(sim):
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    db = Database(":memory:")
    world = WorldTracker(db, client)
    intel = Intelligence(db, world)
    poller = Poller(client, SnapshotStore(), Settings(poll_interval=0.01), world, intel)
    return world, intel, poller


def test_each_snapshot_trains_the_forecaster_and_refreshes_the_assessments():
    sim = FakeSim()
    world, intel, poller = build(sim)

    async def scenario():
        await poller.poll_once()
        assert intel.assessed_tick == 0 and len(intel.assessments) == 3  # prior only, but there is an answer
        assert {a.confidence for a in intel.assessments} == {"prior"}
        sim.step(10)
        poller.request_refresh()
        await poller.poll_once()

    run(scenario())
    assert intel.assessed_tick == 10 and intel.forecaster.trained_through == 9
    assert {a.confidence for a in intel.assessments} == {"low"}
    assert intel.last_error is None


def test_reset_discards_what_was_learned():
    sim = FakeSim()
    world, intel, poller = build(sim)

    async def scenario():
        sim.step(10)
        await poller.poll_once()
        assert intel.forecaster.trained_through == 9
        sim.reset()
        poller.request_refresh()
        await poller.poll_once()

    run(scenario())
    assert intel.forecaster.trained_through == -1 and intel.assessed_tick == 0
    assert world.resets_total == 1


def test_the_api_serves_risks_and_forecast_and_validates_input():
    sim = FakeSim()
    sim.step(8)
    settings = Settings(poll_interval=0.01, ready_max_age=60, database_path=":memory:")
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    with TestClient(create_app(settings=settings, client=client)) as api:
        end = time.time() + 3
        while time.time() < end and not api.get("/api/risks").json():
            time.sleep(0.02)
        risks = api.get("/api/risks").json()
        assert len(risks) == 3 and {"station_id", "fuel_type", "level", "hours_p50", "hours_p90", "risk", "confidence"} <= set(risks[0])
        forecast = api.get("/api/forecast", params={"station_id": "station-mirpur", "fuel": "DIESEL", "ticks": 6}).json()
        assert len(forecast) == 6 and all(r["p10"] <= r["p50"] <= r["p90"] for r in forecast)
        assert api.get("/api/forecast", params={"ticks": 500}).json()["error"]["code"] == "VALIDATION_ERROR"
        meta = api.get("/api/snapshot").json()["meta"]
        assert meta["forecast"]["assessed_tick"] == 8 and meta["forecast"]["error"] is None
