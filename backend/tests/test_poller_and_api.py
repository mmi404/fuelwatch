import asyncio

import httpx
from fastapi.testclient import TestClient

from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.main import create_app
from fuelwatch.poller import Poller
from fuelwatch.snapshot import SnapshotStore

from .conftest import make_world


def run(coro):
    return asyncio.run(coro)


class Switchable:
    """A fake simulator that can be made to fail or to advance its tick."""

    def __init__(self) -> None:
        self.tick = 0
        self.down = False
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.down:
            return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "down"}})
        world = make_world(tick=self.tick)
        return httpx.Response(200, json=world[request.url.path])


def build(fake: Switchable, **settings_kwargs):
    settings = Settings(poll_interval=0.01, **settings_kwargs)
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(fake.handler))
    store = SnapshotStore()
    return client, store, Poller(client, store, settings), settings


def test_first_poll_publishes_a_snapshot():
    fake = Switchable()
    _, store, poller, _ = build(fake)
    assert run(poller.poll_once()) is True
    assert store.current is not None and store.current.tick == 0


def test_unchanged_tick_does_not_reread_the_world():
    fake = Switchable()
    _, store, poller, _ = build(fake)

    async def scenario():
        await poller.poll_once()
        calls_after_first = fake.calls
        published = await poller.poll_once()
        return published, fake.calls - calls_after_first

    published, extra_calls = run(scenario())
    assert published is False
    assert extra_calls == 1  # only the cheap /v1/instance call


def test_new_tick_triggers_a_refresh():
    fake = Switchable()
    _, store, poller, _ = build(fake)

    async def scenario():
        await poller.poll_once()
        fake.tick = 3
        return await poller.poll_once()

    assert run(scenario()) is True
    assert store.current.tick == 3


def test_failure_keeps_last_good_snapshot_and_counts():
    fake = Switchable()
    _, store, poller, _ = build(fake)

    async def scenario():
        await poller.poll_once()
        good = store.current
        fake.down = True
        await poller.poll_once()
        await poller.poll_once()
        return good

    good = run(scenario())
    assert store.current is good  # not replaced
    assert store.consecutive_failures == 2
    assert "FAULT_INJECTED" in store.last_error


def test_recovery_clears_failures():
    fake = Switchable()
    _, store, poller, _ = build(fake)

    async def scenario():
        await poller.poll_once()
        fake.down = True
        await poller.poll_once()
        fake.down = False
        fake.tick = 1
        await poller.poll_once()

    run(scenario())
    assert store.consecutive_failures == 0
    assert store.last_error is None
    assert store.current.tick == 1


def test_request_refresh_forces_full_read_on_same_tick():
    fake = Switchable()
    _, store, poller, _ = build(fake)

    async def scenario():
        await poller.poll_once()
        poller.request_refresh()
        return await poller.poll_once()

    assert run(scenario()) is True


# -- HTTP API ---------------------------------------------------------------------


def make_app(fake: Switchable):
    settings = Settings(poll_interval=0.01, ready_max_age=60)
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(fake.handler))
    return create_app(settings=settings, client=client)


def test_livez_always_ok():
    with TestClient(make_app(Switchable())) as api:
        assert api.get("/livez").json() == {"status": "ok"}


def test_snapshot_and_readyz_when_healthy():
    with TestClient(make_app(Switchable())) as api:
        _wait_for_snapshot(api)
        body = api.get("/api/snapshot").json()
        assert body["meta"]["degraded"] is False
        assert body["world"]["instance"]["tick_minutes"] == 15
        assert len(body["world"]["stations"]) == 1
        ready = api.get("/readyz")
        assert ready.status_code == 200 and ready.json()["ready"] is True


def test_snapshot_503_before_first_read_and_readyz_not_ready():
    fake = Switchable()
    fake.down = True
    with TestClient(make_app(fake)) as api:
        assert api.get("/api/snapshot").status_code == 503
        ready = api.get("/readyz")
        assert ready.status_code == 503 and ready.json()["ready"] is False


def test_degraded_flag_when_simulator_fails_after_a_good_read():
    fake = Switchable()
    with TestClient(make_app(fake)) as api:
        _wait_for_snapshot(api)
        fake.down = True
        _wait_until(lambda: api.get("/api/snapshot").json()["meta"]["degraded"])
        meta = api.get("/api/snapshot").json()["meta"]
        assert meta["degraded"] is True
        assert meta["has_snapshot"] is True  # still serving the last good one
        assert meta["last_error"]


def _wait_until(predicate, timeout=3.0):
    import time

    end = time.time() + timeout
    while time.time() < end:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def _wait_for_snapshot(api):
    _wait_until(lambda: api.get("/api/snapshot").status_code == 200)
