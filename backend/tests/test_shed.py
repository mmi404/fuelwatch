import asyncio
import re

import httpx
import pytest
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute

from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.events import EventBus, sse_stream
from fuelwatch.main import create_app, error_body
from fuelwatch.metrics import SHED_PRIORITIES, Observability
from fuelwatch.shed import ESSENTIAL, EXEMPT, OPTIONAL, STREAM, LoadShedMiddleware, classify

from .conftest import Sleeps
from .test_executor import ExecSim, run
from .test_metrics import sample

CONSOLE_REFRESH = ("/api/status", "/api/snapshot", "/api/risks", "/api/proposals", "/api/alerts", "/api/anomalies")
HEALTH_BOARD = "/api/health/components"
CONSOLE_EXTRAS = ("/api/allocations", "/api/audit", "/api/forecast")
PROTECTED = (
    ("GET", "/livez"), ("GET", "/readyz"), ("GET", "/metrics"),
    ("POST", "/api/proposals/7/approve"), ("POST", "/api/proposals/7/reject"), ("PUT", "/api/autopilot"),
    ("POST", "/api/sim/run"), ("POST", "/api/sim/pause"), ("POST", "/api/sim/step"), ("POST", "/api/sim/reset"),
)


class Held:
    """An inner app whose requests stay in flight until released, or fail on `/api/boom`."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = 0

    async def __call__(self, scope, receive, send):
        self.entered += 1
        if scope["path"] == "/api/boom":
            raise RuntimeError("boom")
        await self.release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def guard(inner, optional=1, essential=2, stream=1, obs=None):
    return LoadShedMiddleware(inner, optional_limit=optional, essential_limit=essential, stream_limit=stream, error_body=error_body, obs=obs or Observability())


async def call(app, path, method="GET"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app({"type": "http", "method": method, "path": path, "headers": []}, receive, send)
    return sent


def status(sent):
    return sent[0]["status"]


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def hold(app, path, method="GET"):
    task = asyncio.create_task(call(app, path, method))
    await settle()
    return task


def test_what_the_console_needs_together_is_essential_and_the_rest_is_optional():
    for path in (*CONSOLE_REFRESH, HEALTH_BOARD, "/api/proposals/7"):
        assert classify("GET", path) == ESSENTIAL, path
    for path in (*CONSOLE_EXTRAS, "/api/autopilot", "/api/not-a-route", "/", "/docs"):
        assert classify("GET", path) == OPTIONAL, path
    assert classify("GET", "/api/stream") == STREAM
    for method, path in PROTECTED:
        assert classify(method, path) == EXEMPT, f"{method} {path}"
    assert classify("POST", "/api/audit") == OPTIONAL  # a POST is not exempt just for being a POST


def make_app(**limits):
    sim = ExecSim()
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    return create_app(settings=Settings(database_path=":memory:", operator_token="s3cret", **limits), client=client)


def test_every_route_that_requires_the_operator_token_is_exempt():
    found = 0
    for route in make_app().routes:
        if isinstance(route, APIRoute) and any(d.call.__name__ == "require_operator" for d in route.dependant.dependencies):
            for method in route.methods - {"GET"}:  # GET /api/me only reads who a key belongs to
                found += 1
                assert classify(method, re.sub(r"\{\w+\}", "7", route.path)) == EXEMPT, f"{method} {route.path}"
    assert found == 4


def test_optional_reads_are_refused_first_and_essential_ones_keep_the_headroom():
    async def scenario():
        inner, obs = Held(), Observability()
        app = guard(inner, optional=1, essential=2, obs=obs)
        first = await hold(app, "/api/forecast")
        refused_optional = await call(app, "/api/audit")
        second = await hold(app, "/api/status")
        refused_essential = await call(app, "/api/snapshot")
        assert app.in_flight == 2 and inner.entered == 2
        inner.release.set()
        assert status(await first) == 200 and status(await second) == 200
        assert app.in_flight == 0
        assert status(await call(app, "/api/audit")) == 200  # served again as soon as the load is gone
        return refused_optional, refused_essential, obs.exposition().decode()

    optional, essential, text = run(scenario())
    assert status(optional) == 503 and status(essential) == 503
    assert sample(text, "fuelwatch_requests_shed_total", priority="optional") == 1
    assert sample(text, "fuelwatch_requests_shed_total", priority="essential") == 1
    assert set(re.findall(r'^fuelwatch_requests_shed_total\{priority="(\w+)"\}', text, re.M)) <= set(SHED_PRIORITIES)


def test_health_probes_the_scrape_and_operator_actions_are_never_refused_or_counted():
    async def scenario():
        inner = Held()
        app = guard(inner, optional=1, essential=1, stream=1)
        busy = [await hold(app, "/api/status"), await hold(app, "/api/stream")]
        assert status(await call(app, "/api/risks")) == 503
        protected = [await hold(app, path, method) for method, path in PROTECTED]
        assert inner.entered == len(busy) + len(PROTECTED)
        assert app.in_flight == 1 and app.streams == 1
        inner.release.set()
        assert [status(await t) for t in protected] == [200] * len(PROTECTED)
        await asyncio.gather(*busy)

    run(scenario())


def test_a_slot_is_released_when_the_handler_raises():
    async def scenario():
        app = guard(Held())
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await call(app, "/api/boom")
        assert app.in_flight == 0

    run(scenario())


@pytest.mark.parametrize("path", ["/api/forecast", "/api/status", "/api/stream"])
@pytest.mark.parametrize("reached_the_app", [False, True])
def test_a_slot_is_released_when_the_request_is_cancelled(path, reached_the_app):
    async def scenario():
        inner = Held()
        app = guard(inner)
        task = asyncio.create_task(call(app, path))
        await asyncio.sleep(0)
        if reached_the_app:
            await settle()
            assert inner.entered == 1
        assert app.in_flight + app.streams == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.in_flight == 0 and app.streams == 0

    run(scenario())


def test_streams_have_their_own_cap_and_a_disconnect_frees_the_place():
    async def scenario():
        bus, gone = EventBus(), asyncio.Event()

        async def inner(scope, receive, send):
            if scope["path"] == "/api/stream":
                await StreamingResponse(sse_stream(bus), media_type="text/event-stream")(scope, receive, send)
            else:
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

        obs = Observability()
        app = guard(inner, optional=1, essential=1, stream=1, obs=obs)
        chunks, asked = [], []

        async def receive():
            if not asked:
                asked.append(True)
                return {"type": "http.request", "body": b"", "more_body": False}
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            chunks.append(message)

        tab = asyncio.create_task(app({"type": "http", "method": "GET", "path": "/api/stream", "headers": []}, receive, send))
        await settle()
        assert app.streams == 1 and app.in_flight == 0 and bus.subscribers == 1
        assert chunks[1]["body"] == b": connected\n\n"  # passed through at once, not buffered
        assert status(await call(app, "/api/status")) == 200  # an open stream takes no request slot
        assert status(await call(app, "/api/stream")) == 503
        gone.set()
        await asyncio.wait_for(tab, 2)
        assert app.streams == 0 and bus.subscribers == 0
        assert sample(obs.exposition().decode(), "fuelwatch_requests_shed_total", priority="stream") == 1

    run(scenario())


def burst(app, requests):
    """Sends (method, path) pairs to the real application all at the same instant."""

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fuelwatch") as http:
            return await asyncio.gather(*(http.request(method, path) for method, path in requests))

    return run(scenario())


def test_a_burst_of_reads_that_never_pause_is_still_limited_and_refused_honestly():
    app = make_app(shed_optional_limit=2, shed_essential_limit=3)
    replies = burst(app, [("GET", "/api/risks")] * 10 + [("GET", "/livez")] * 5 + [("POST", "/api/sim/step")] * 5)
    reads, probes, actions = replies[:10], replies[10:15], replies[15:]
    assert sorted(r.status_code for r in reads) == [200] * 3 + [503] * 7
    refused = next(r for r in reads if r.status_code == 503)
    assert refused.json() == {"error": {"code": "OVERLOADED", "message": "the server is busy; try again in 5 s"}}
    assert refused.headers["retry-after"] == "5" and refused.headers["x-request-id"]
    assert [r.status_code for r in probes] == [200] * 5
    assert [r.status_code for r in actions] == [401] * 5  # they reach the token check: the load does not stop them
    text = app.state.obs.exposition().decode()
    assert sample(text, "fuelwatch_requests_shed_total", priority="essential") == 7
    assert sample(text, "fuelwatch_http_request_seconds_count", route="unmatched", method="GET", status_class="5xx") == 7


def test_the_default_limits_let_ten_tabs_ask_for_everything_at_the_same_instant():
    app = make_app()
    replies = burst(app, [("GET", path) for path in (*CONSOLE_EXTRAS, *CONSOLE_REFRESH, HEALTH_BOARD)] * 10)
    assert len(replies) == 100
    assert all("retry-after" not in r.headers for r in replies)
    assert "fuelwatch_requests_shed_total{" not in app.state.obs.exposition().decode()


def test_a_limit_of_zero_turns_shedding_off():
    app = make_app(shed_optional_limit=0, shed_essential_limit=0, shed_stream_limit=0)
    replies = burst(app, [("GET", "/api/risks"), ("GET", "/api/audit")] * 150)
    assert {r.status_code for r in replies} == {200}


def test_the_limits_are_read_from_the_environment(monkeypatch):
    defaults = Settings()
    assert (defaults.shed_optional_limit, defaults.shed_essential_limit, defaults.shed_stream_limit) == (128, 256, 200)
    monkeypatch.setenv("SHED_OPTIONAL_LIMIT", "0")
    monkeypatch.setenv("SHED_ESSENTIAL_LIMIT", "12")
    monkeypatch.setenv("SHED_STREAM_LIMIT", "3")
    s = Settings.from_env()
    assert (s.shed_optional_limit, s.shed_essential_limit, s.shed_stream_limit) == (0, 12, 3)
