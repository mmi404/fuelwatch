import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.db import Database
from fuelwatch.main import create_app
from fuelwatch.poller import Poller
from fuelwatch.snapshot import SnapshotStore
from fuelwatch.world import WorldTracker, detect_reset

from .conftest import Sleeps, make_world

FUELS = ("DIESEL", "PETROL", "OCTANE")


def run(coro):
    return asyncio.run(coro)


class FakeSim:
    """A simulator whose tick can advance and which can be reset, with newest-first demand history."""

    def __init__(self) -> None:
        self.tick = 0
        self.served = 0.0
        self.allocations = 2
        self.history_calls: list[int] = []
        self.history_down = False
        self.seed = 12345
        self.cap = 2000  # unfiltered responses
        self.station_cap = 2000  # responses filtered by station
        self.history_params: list[dict] = []

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.served += 10.0
            self.tick += 1

    def reset(self) -> None:
        self.tick, self.served, self.allocations = 0, 0.0, 0

    def history(self, limit: int, station_id: str | None = None) -> list[dict]:
        rows = []
        rid = 0
        for t in range(self.tick):
            for f in FUELS:
                rid += 1
                rows.append({"id": rid, "station_id": "station-mirpur", "fuel_type": f, "tick": t, "sim_time": "2026-01-01T00:00:00", "demand_liters": 100.0 + t, "served_liters": 100.0 + t, "unmet_liters": 0.0})
        rows.sort(key=lambda r: -r["id"])
        if station_id is not None:
            rows = [r for r in rows if r["station_id"] == station_id]
        return rows[: min(limit, self.station_cap if station_id is not None else self.cap)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/demand-history":
            self.history_calls.append(int(request.url.params["limit"]))
            self.history_params.append(dict(request.url.params))
            if self.history_down:
                return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "down"}})
            return httpx.Response(200, json=self.history(int(request.url.params["limit"]), request.url.params.get("station_id")))
        world = make_world(tick=self.tick)
        world["/v1/instance"]["seed"] = self.seed
        world["/v1/metrics"]["served_demand_liters"] = self.served
        world["/v1/allocations"] = world["/v1/allocations"][: self.allocations]
        return httpx.Response(200, json=world[path])


def build(sim: FakeSim, db: Database | None = None):
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    db = db or Database(":memory:")
    world = WorldTracker(db, client)
    store = SnapshotStore()
    poller = Poller(client, store, Settings(poll_interval=0.01), world)
    return db, world, store, poller


async def cycle(poller, sim, steps=1):
    sim.step(steps)
    poller.request_refresh()
    await poller.poll_once()


# -- database -------------------------------------------------------------------------


def test_database_uses_wal_and_versions_its_schema(tmp_path):
    path = str(tmp_path / "sub" / "fw.db")
    db = Database(path)
    assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1
    db.close()
    Database(path).close()  # reopening an existing file is fine


# -- epochs and reset detection ---------------------------------------------------------


def test_first_snapshot_starts_epoch_one_without_a_reset_event():
    sim = FakeSim()
    _, world, _, poller = build(sim)
    run(poller.poll_once())
    assert world.epoch.id == 1 and world.epoch.reason == "first_seen"
    assert world.resets_total == 0


def test_normal_progress_stays_in_the_same_epoch():
    sim = FakeSim()
    _, world, _, poller = build(sim)

    async def scenario():
        await poller.poll_once()
        for _ in range(5):
            await cycle(poller, sim)

    run(scenario())
    assert world.epoch.id == 1 and world.epoch.last_tick == 5 and world.resets_total == 0


def test_tick_going_backwards_starts_a_new_epoch_and_notifies():
    sim = FakeSim()
    _, world, store, poller = build(sim)
    seen = []
    world.on_reset(seen.append)

    async def scenario():
        await poller.poll_once()
        await cycle(poller, sim, 10)
        sim.reset()
        poller.request_refresh()
        await poller.poll_once()

    run(scenario())
    assert world.epoch.id == 2 and world.epoch.reason == "tick_regression"
    assert world.resets_total == 1 and seen[0].old_epoch_id == 1 and seen[0].new_epoch_id == 2
    assert store.current.tick == 0


def test_reset_that_runs_past_the_old_tick_is_caught_by_the_counters():
    sim = FakeSim()
    _, world, _, poller = build(sim)

    async def scenario():
        await poller.poll_once()
        await cycle(poller, sim, 3)
        sim.reset()
        sim.step(5)  # already beyond tick 3 by the time we look
        sim.served = 1.0  # but the running total is lower than the 30 we saw
        poller.request_refresh()
        await poller.poll_once()

    run(scenario())
    assert world.epoch.id == 2 and world.epoch.reason == "counters_regression"


def test_changed_seed_is_a_scenario_change():
    sim = FakeSim()
    _, world, _, poller = build(sim)

    async def scenario():
        await poller.poll_once()
        sim.seed = 999
        poller.request_refresh()
        await poller.poll_once()

    run(scenario())
    assert world.epoch.id == 2 and world.epoch.reason == "scenario_change"


def test_detect_reset_is_quiet_when_nothing_changed():
    sim = FakeSim()
    _, world, store, poller = build(sim)
    run(poller.poll_once())
    assert detect_reset(world.epoch, store.current) is None


def test_epoch_survives_a_restart_and_a_reset_while_down_is_noticed(tmp_path):
    path = str(tmp_path / "fw.db")
    sim = FakeSim()

    async def first_run():
        db, world, _, poller = build(sim, Database(path))
        await poller.poll_once()
        await cycle(poller, sim, 6)
        db.close()

    run(first_run())

    async def restart_same_world():
        db, world, _, poller = build(sim, Database(path))
        assert world.epoch.id == 1  # loaded from disk
        await poller.poll_once()
        assert world.epoch.id == 1 and world.resets_total == 0
        db.close()

    run(restart_same_world())

    async def restart_after_reset():
        sim.reset()
        db, world, _, poller = build(sim, Database(path))
        await poller.poll_once()
        assert world.epoch.id == 2 and world.epoch.reason == "tick_regression"
        db.close()

    run(restart_after_reset())


# -- demand history ---------------------------------------------------------------------


def test_history_is_synced_in_full_then_incrementally():
    sim = FakeSim()
    db, world, _, poller = build(sim)

    async def scenario():
        sim.step(10)
        await poller.poll_once()
        assert world.synced_through == 9 and world.history_from == 0 and world.history_rows == 30
        first = list(sim.history_calls)
        await cycle(poller, sim, 2)
        return first

    first = run(scenario())
    assert first == [33]  # 10 missing ticks plus one of slack, 3 rows each: sized from the gap, not a fixed 200
    assert sim.history_calls[-1] == 9  # two new ticks: (2 + 1) x 3 rows
    assert world.synced_through == 11 and world.history_rows == 36


def test_no_request_when_history_is_already_complete():
    sim = FakeSim()
    _, world, _, poller = build(sim)

    async def scenario():
        sim.step(4)
        await poller.poll_once()
        calls = len(sim.history_calls)
        poller.request_refresh()
        await poller.poll_once()  # same tick again
        return calls

    assert run(scenario()) == len(sim.history_calls)


def test_history_failure_is_reported_but_does_not_block_the_snapshot_and_heals_later():
    sim = FakeSim()
    _, world, store, poller = build(sim)

    async def scenario():
        sim.step(5)
        sim.history_down = True
        await poller.poll_once()
        assert store.current is not None and store.consecutive_failures == 0
        assert world.history_error and world.synced_through == -1
        sim.history_down = False
        await cycle(poller, sim)  # next cycle asks for everything missing
        assert world.history_error is None and world.synced_through == 5

    run(scenario())


def test_rows_of_the_old_epoch_stay_separate_from_the_new_one():
    sim = FakeSim()
    db, world, _, poller = build(sim)

    async def scenario():
        sim.step(6)
        await poller.poll_once()
        sim.reset()
        sim.step(2)
        poller.request_refresh()
        await poller.poll_once()

    run(scenario())
    assert db.demand_row_count(1) == 18 and db.demand_row_count(2) == 6
    assert world.synced_through == 1
    assert [r["tick"] for r in db.demand_rows(2, fuel_type="DIESEL")] == [0, 1]


def test_complete_run_ignores_partial_ticks_and_keeps_only_the_newest_run():
    db = Database(":memory:")
    e = db.insert_epoch(reason="first_seen", instance_id=1, scenario_id="b", scenario_version="1", seed=1, tick_minutes=15, tick=0, allocation_count=0, served_liters=0)
    from fuelwatch.models import DemandRecord

    def rec(tick, fuel):
        return DemandRecord(id=1, station_id="s", fuel_type=fuel, tick=tick, sim_time="2026-01-01T00:00:00", demand_liters=1, served_liters=1, unmet_liters=0)

    assert db.complete_run(e.id, 3) is None
    rows = [rec(0, f) for f in FUELS] + [rec(1, f) for f in FUELS] + [rec(3, f) for f in FUELS] + [rec(2, "DIESEL")]
    db.upsert_demand(e.id, rows)
    assert db.complete_run(e.id, 3) == (3, 3)  # tick 2 is partial, so the run after the gap is the newest
    db.upsert_demand(e.id, [rec(2, "PETROL"), rec(2, "OCTANE")])
    assert db.complete_run(e.id, 3) == (0, 3)
    db.upsert_demand(e.id, [rec(2, "PETROL")])  # an upsert of an existing row changes nothing
    assert db.demand_row_count(e.id) == 12


def test_a_window_longer_than_the_endpoint_cap_is_read_station_by_station():
    sim = FakeSim()
    sim.cap = 15  # tiny global cap: 5 ticks; the per-station cap stays large
    import fuelwatch.world as w

    w.HISTORY_PAGE_CAP = 15
    try:
        _, world, _, poller = build(sim)

        async def scenario():
            sim.step(3)  # (3 + 1 slack) x 3 = 12 rows: fits in one global request
            await poller.poll_once()
            assert "station_id" not in sim.history_params[-1]
            await cycle(poller, sim, 17)  # 17 ticks missing: (17 + 1) x 3 = 54 rows, over the cap

        run(scenario())
    finally:
        w.HISTORY_PAGE_CAP = 2000
    assert any("station_id" in p for p in sim.history_params)
    assert world.synced_through == 19 and world.history_from == 0 and world.history_rows == 60


def test_a_window_beyond_even_the_station_cap_is_truncated_to_the_newest_ticks():
    sim = FakeSim()  # caps of 2,000 rows like the simulator; the fake has 3 rows per tick, so 700 ticks is 2,100 rows
    _, world, _, poller = build(sim)
    sim.step(700)
    run(poller.poll_once())
    # 2,000 rows is 666 whole ticks: the newest window is complete and contiguous, the oldest ticks are simply not held
    assert world.synced_through == 699 and world.history_from == 34 and world.history_rows == 2000


def test_restart_with_a_gap_fetches_only_the_missing_ticks(tmp_path):
    path = str(tmp_path / "fw.db")
    sim = FakeSim()

    async def first():
        db, world, _, poller = build(sim, Database(path))
        sim.step(5)
        await poller.poll_once()
        db.close()

    run(first())
    sim.history_calls.clear()

    async def second():
        db, world, _, poller = build(sim, Database(path))
        sim.step(4)  # we were down for 4 ticks
        await poller.poll_once()
        assert world.synced_through == 8 and world.history_from == 0
        db.close()

    run(second())
    assert sim.history_calls == [15]  # (4 missing + 1) x 3 rows, not the whole history


# -- through the HTTP API -----------------------------------------------------------------


def test_meta_reports_epoch_and_history():
    sim = FakeSim()
    sim.step(4)
    settings = Settings(poll_interval=0.01, ready_max_age=60, database_path=":memory:")
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    with TestClient(create_app(settings=settings, client=client)) as api:
        import time

        end = time.time() + 3
        while time.time() < end:
            r = api.get("/api/snapshot")
            if r.status_code == 200 and r.json()["meta"]["history"]["synced_through_tick"] == 3:
                break
            time.sleep(0.02)
        meta = api.get("/api/snapshot").json()["meta"]
    assert meta["epoch"]["id"] == 1 and meta["history"]["rows"] == 12 and meta["resets_total"] == 0
