import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.events import EventBus, sse_stream
from fuelwatch.main import create_app
from fuelwatch.stream import StreamListener

from .conftest import Sleeps
from .test_executor import ExecSim, Rig, _first_diesel, make_api, proposed, run

H = {"X-Operator-Token": "s3cret"}


# -- autopilot -----------------------------------------------------------------------------


def test_autopilot_is_off_by_default_and_approves_nothing():
    async def scenario():
        rig, p = await proposed()
        assert rig.executor.autopilot.enabled is False
        await rig.executor.after_poll()
        return rig, p

    rig, p = run(scenario())
    assert rig.sim.posts == [] and rig.db.proposal(p["id"])["status"] == "PROPOSED"


def test_autopilot_approves_within_its_guards_and_audits_who_acted():
    async def scenario():
        rig, p = await proposed()
        rig.executor.set_autopilot({"enabled": True})
        await rig.poll()
        await rig.executor.after_poll()
        return rig, p

    rig, p = run(scenario())
    assert rig.db.proposal(p["id"])["status"] == "EXECUTED" and len(rig.sim.posts) == 1
    approved = [r for r in rig.db.audit_rows() if r["action"] == "proposal.approved"]
    assert approved and approved[0]["actor"] == "autopilot"
    assert any(r["action"] == "autopilot.changed" for r in rig.db.audit_rows())


def test_autopilot_leaves_low_confidence_and_oversized_proposals_to_a_human():
    async def scenario():
        rig, p = await proposed()
        rig.executor.set_autopilot({"enabled": True, "min_confidence": 0.99})  # the prior-only forecast scores 0.7
        await rig.poll()
        await rig.executor.after_poll()
        assert rig.sim.posts == []
        rig.executor.set_autopilot({"min_confidence": 0.5, "max_liters_per_proposal": 100})
        await rig.poll()
        await rig.executor.after_poll()
        return rig

    rig = run(scenario())
    assert rig.sim.posts == [] and rig.executor.counters["autopilot_skipped_guard"] >= 2


def test_autopilot_stops_at_the_per_tick_budget():
    async def scenario():
        rig, _ = await proposed()
        rig.executor.set_autopilot({"enabled": True, "max_proposals_per_tick": 1, "max_liters_per_tick": 1})
        await rig.poll()
        await rig.executor.after_poll()
        return rig

    rig = run(scenario())
    assert rig.sim.posts == [] and rig.executor.counters["autopilot_skipped_budget"] >= 1


def test_autopilot_never_overrides_a_closed_gate():
    async def scenario():
        rig, p = await proposed()
        rig.executor.set_autopilot({"enabled": True})
        rig.sim.stale = True
        await rig.poll()
        await rig.executor.after_poll()
        return rig, p

    rig, p = run(scenario())
    assert rig.sim.posts == [] and rig.db.proposal(p["id"])["status"] == "PROPOSED"


# -- API ----------------------------------------------------------------------------------


def test_status_allocations_audit_and_autopilot_endpoints():
    sim, app = make_api("s3cret")
    with app as api:
        p = _first_diesel(api)
        assert api.put("/api/autopilot", json={"enabled": True}).status_code == 401
        assert api.put("/api/autopilot", headers=H, json={"max_liters_per_tick": 0}).json()["error"]["code"] == "VALIDATION_ERROR"
        assert api.get("/api/autopilot").json()["enabled"] is False  # nothing changed

        assert api.post(f"/api/proposals/{p['id']}/approve", headers=H).status_code == 200
        ledger = api.get("/api/allocations").json()
        assert len(ledger) == 1 and ledger[0]["status"] == "SUBMITTED" and ledger[0]["proposal_id"] == p["id"]
        assert api.get("/api/allocations", params={"status": "ARRIVED"}).json() == []
        assert api.get("/api/allocations", params={"limit": 0}).status_code == 422

        audit = api.get("/api/audit", params={"limit": 2}).json()
        assert len(audit) == 2 and audit[0]["id"] > audit[1]["id"] and isinstance(audit[0]["detail"], dict)
        assert api.get("/api/audit", params={"offset": -1}).status_code == 422

        status = api.get("/api/status").json()
        assert status["mode"] in ("LIVE", "DEGRADED") and status["execution"]["lines_submitted"] == 1
        assert status["proposals_by_status"].get("EXECUTED") == 1 and status["versions"]["schema"] == 3
        assert status["stream"]["state"] == "DISCONNECTED" and status["stream"]["enabled"] is False
        assert status["autopilot"]["enabled"] is False


def test_autopilot_can_be_switched_through_the_api():
    _, app = make_api("s3cret")
    with app as api:
        r = api.put("/api/autopilot", headers=H, json={"enabled": True, "max_liters_per_proposal": 5000})
        assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["max_liters_per_proposal"] == 5000
        assert api.get("/api/autopilot").json()["max_liters_per_tick"] == 60000  # untouched fields keep their value


def test_sim_controls_are_proxied_audited_and_protected():
    sim, app = make_api("s3cret")
    with app as api:
        assert api.post("/api/sim/step").status_code == 401
        assert api.post("/api/sim/explode", headers=H).status_code == 404
        for action in ("run", "pause", "step", "reset"):
            assert api.post(f"/api/sim/{action}", headers=H).json()["ok"] is True
        assert sim.admin_calls == ["/admin/run", "/admin/pause", "/admin/step", "/admin/reset"]
        actions = [r["action"] for r in api.get("/api/audit").json()]
        assert actions.count("sim.step") == 1
        sim.admin_down = True
        bad = api.post("/api/sim/step", headers=H)
        assert bad.status_code == 503 and bad.json()["error"]["code"] == "SIMULATOR_UNAVAILABLE"


# -- browser stream --------------------------------------------------------------------------


def test_sse_stream_sends_state_then_events_then_keepalives():
    async def scenario():
        bus = EventBus()
        gen = sse_stream(bus, initial=[("snapshot", {"tick": 7})], keepalive=0.05)
        first = await gen.__anext__()
        state = await gen.__anext__()
        bus.publish("proposal", {"id": 3, "status": "EXECUTED"})
        event = await gen.__anext__()
        ping = await gen.__anext__()
        await gen.aclose()
        return bus, first, state, event, ping

    bus, first, state, event, ping = run(scenario())
    assert first.startswith(": connected") and state == 'event: snapshot\ndata: {"tick": 7}\n\n'
    assert event == 'event: proposal\ndata: {"id": 3, "status": "EXECUTED"}\n\n' and ping.startswith(": keepalive")
    assert bus.subscribers == 0  # unsubscribed when the client went away


def test_a_slow_subscriber_loses_old_events_and_never_blocks_the_publisher():
    bus = EventBus(queue_size=2)
    q = bus.subscribe()
    for i in range(5):
        bus.publish("tick", {"i": i})
    assert [q.get_nowait()[1]["i"] for _ in range(2)] == [3, 4] and bus.dropped == 3


# -- the simulator's tick stream ------------------------------------------------------------


class FakePoller:
    def __init__(self) -> None:
        self.wakes = 0

    def wake(self) -> None:
        self.wakes += 1


def stream_client(handler):
    return SimulatorClient("http://sim", transport=httpx.MockTransport(handler), sleep=Sleeps())


def test_the_listener_wakes_the_poller_on_tick_events_and_reconnects_after_the_stream_closes():
    body = b': connected\n\nevent: simulation.tick\ndata: {"tick": 1}\n\nevent: simulation.tick\ndata: {"tick": 2}\n\n'
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    async def scenario():
        from fuelwatch.snapshot import SnapshotStore

        poller, delays = FakePoller(), []

        async def sleep(seconds):
            delays.append(seconds)
            if len(delays) >= 2:
                raise asyncio.CancelledError

        listener = StreamListener(stream_client(handler), SnapshotStore(), poller, sleep=sleep)
        try:
            await listener.run()
        except asyncio.CancelledError:
            pass
        return listener, poller, delays

    listener, poller, delays = run(scenario())
    assert calls == ["/v1/stream", "/v1/stream"] and listener.connects == 2 and listener.tick_events == 4
    assert listener.last_event_tick == 2 and poller.wakes == 5  # four ticks and the refresh after the reconnect
    assert listener.state == "BACKOFF" and delays == [1.0, 1.0]  # the backoff restarts after a good connection


def test_a_refused_stream_backs_off_exponentially_and_reports_the_error():
    def handler(request):
        return httpx.Response(503, json={"detail": {"code": "STREAM_UNAVAILABLE", "message": "fault"}})

    async def scenario():
        from fuelwatch.snapshot import SnapshotStore

        delays = []

        async def sleep(seconds):
            delays.append(seconds)
            if len(delays) >= 4:
                raise asyncio.CancelledError

        listener = StreamListener(stream_client(handler), SnapshotStore(), FakePoller(), sleep=sleep)
        try:
            await listener.run()
        except asyncio.CancelledError:
            pass
        return listener, delays

    listener, delays = run(scenario())
    assert delays == [1.0, 2.0, 4.0, 8.0] and listener.connects == 0 and "STREAM_UNAVAILABLE" in listener.last_error


def test_a_connection_that_stays_open_but_sends_no_tick_events_is_declared_stalled():
    from fuelwatch.snapshot import SnapshotStore

    class Silent(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": connected\n\n"
            await asyncio.sleep(3600)

    def handler(request):
        return httpx.Response(200, stream=Silent(), headers={"content-type": "text/event-stream"})

    async def scenario():
        sim = ExecSim()
        rig = Rig(sim)
        await rig.poll()  # a snapshot at tick 0 exists, and the REST tick will "move" below
        store = rig.store
        clock_value = [0.0]

        def clock():
            clock_value[0] += 4.0  # every look at the clock is four seconds later
            return clock_value[0]

        sim.tick = 2
        await rig.poll()
        listener = StreamListener(stream_client(handler), store, FakePoller(), stall_seconds=10.0, check_every=0.02, clock=clock, sleep=lambda s: asyncio.sleep(0))
        task = asyncio.create_task(listener.run())
        for _ in range(200):
            if listener.stalls:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return listener

    listener = run(scenario())
    assert listener.stalls >= 1 and listener.connects >= 1
