import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from fuelwatch import executor as executor_module
from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.db import Database
from fuelwatch.executor import ApiError, Executor
from fuelwatch.health import Mode, compute_mode
from fuelwatch.intel import Intelligence
from fuelwatch.main import create_app
from fuelwatch.poller import Poller
from fuelwatch.proposals import ProposalService
from fuelwatch.snapshot import SnapshotStore
from fuelwatch.world import WorldTracker

from .conftest import Sleeps, make_world


def run(coro):
    return asyncio.run(coro)


class ExecSim:
    """A fake simulator with a low diesel tank, a working POST /v1/allocations and switches for the failure cases."""

    def __init__(self, tick: int = 0, diesel: float = 600.0) -> None:
        self.tick = tick
        self.diesel = diesel
        self.depot_diesel = 60000.0
        self.events: list[dict] = []
        self.stale = False
        self.post_mode = "ok"  # ok, reject, drop_response (recorded, then a 503), fail (not recorded, 503)
        self.reject_code = "ROUTE_DISRUPTED"
        self.allocations: dict[str, dict] = {}
        self.posts: list[dict] = []

    def _world(self) -> dict:
        w = make_world(tick=self.tick)
        w["/v1/stations"][0]["inventory"]["DIESEL"] = self.diesel
        w["/v1/depots"][0]["inventory"]["DIESEL"] = self.depot_diesel
        w["/v1/events"] = self.events
        w["/v1/allocations"] = list(self.allocations.values())
        return w

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/allocations":
            import json

            body = json.loads(request.content)
            self.posts.append(body)
            if self.post_mode == "reject":
                return httpx.Response(409, json={"detail": {"code": self.reject_code, "message": "no"}})
            if self.post_mode == "fail":
                return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "down"}})
            alloc = self.allocations.get(body["idempotency_key"]) or {
                "id": len(self.allocations) + 10, **{k: body[k] for k in ("idempotency_key", "source_depot_id", "destination_station_id", "route_id", "fuel_type", "quantity")},
                "created_tick": self.tick, "departure_tick": None, "expected_arrival_tick": None, "actual_arrival_tick": None, "status": "PENDING", "failure_reason": None,
            }
            self.allocations[body["idempotency_key"]] = alloc
            if self.post_mode == "drop_response":
                return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "response lost"}})
            return httpx.Response(201, json=alloc)
        if path == "/v1/demand-history":
            return httpx.Response(200, json=[])
        headers = {"X-Simulator-Stale": "true"} if self.stale else {}
        return httpx.Response(200, json=self._world()[path], headers=headers)


class Rig:
    def __init__(self, sim: ExecSim, **settings) -> None:
        self.sim = sim
        self.settings = Settings(poll_interval=0.01, database_path=":memory:", **settings)
        self.client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
        self.db = Database(":memory:")
        self.store = SnapshotStore()
        self.world = WorldTracker(self.db, self.client)
        proposals = ProposalService(self.db, self.world)
        self.intel = Intelligence(self.db, self.world, proposals=proposals)
        self.mode = lambda: compute_mode(self.store.current, consecutive_failures=self.store.consecutive_failures, any_breaker_not_closed=self.client.any_breaker_not_closed(), stale_after=self.settings.ready_max_age)
        self.executor = Executor(self.db, self.world, self.store, self.client, self.intel, self.settings, self.mode)
        self.poller = Poller(self.client, self.store, self.settings, self.world, self.intel, self.executor)
        self.executor.attach(self.poller)

    async def poll(self) -> None:
        self.poller.request_refresh()
        await self.poller.poll_once()

    def diesel_proposal(self) -> dict:
        (p,) = [p for p in self.db.proposals(epoch_id=self.world.epoch.id, statuses=("PROPOSED",)) if p["fuel_type"] == "DIESEL"]
        return p

    def actions(self) -> list[str]:
        return [r["action"] for r in reversed(self.db.audit_rows(limit=100))]


async def proposed(sim: ExecSim | None = None, **kw) -> tuple[Rig, dict]:
    rig = Rig(sim or ExecSim(), **kw)
    await rig.poll()
    return rig, rig.diesel_proposal()


def test_approving_sends_one_idempotent_request_and_records_everything():
    async def scenario():
        rig, p = await proposed()
        out = await rig.executor.approve(p["id"])
        return rig, p, out

    rig, p, out = run(scenario())
    assert out["status"] == "EXECUTED"
    (line,) = out["lines"]
    assert line["status"] == "SUBMITTED" and line["sim_allocation_id"] is not None
    assert line["executed_quantity"] <= line["proposed_quantity"]
    (post,) = rig.sim.posts
    assert post["idempotency_key"] == f"fw-p{p['id']}-l{line['id']}" and post["quantity"] == line["executed_quantity"]
    assert [a["outcome"] for a in line["attempts"]] == ["SENDING", "SUBMITTED"]
    assert rig.actions() == ["proposal.approved", "line.submitted", "proposal.executed"]


def test_unknown_and_already_decided_proposals_are_refused():
    async def scenario():
        rig, p = await proposed()
        await rig.executor.approve(p["id"])
        with pytest.raises(ApiError) as again:
            await rig.executor.approve(p["id"])
        with pytest.raises(ApiError) as missing:
            await rig.executor.approve(9999)
        return again.value, missing.value

    again, missing = run(scenario())
    assert (again.status, again.code) == (409, "INVALID_STATE") and (missing.status, missing.code) == (404, "NOT_FOUND")


def test_reject_closes_the_proposal_and_sends_nothing():
    async def scenario():
        rig, p = await proposed()
        out = await rig.executor.reject(p["id"])
        return rig, out

    rig, out = run(scenario())
    assert out["status"] == "REJECTED" and [l["status"] for l in out["lines"]] == ["SKIPPED"] and rig.sim.posts == []


def test_quantity_can_only_shrink_when_the_depot_has_less_than_when_proposed():
    async def scenario():
        rig, p = await proposed()
        rig.sim.depot_diesel = 900.0  # stock dropped after the proposal was made
        return p, await rig.executor.approve(p["id"]), rig

    p, out, rig = run(scenario())
    (line,) = out["lines"]
    assert line["executed_quantity"] == 900 < p["lines"][0]["proposed_quantity"]
    assert out["status"] == "EXECUTED" and rig.sim.posts[0]["quantity"] == 900


def test_a_line_whose_route_is_now_disrupted_is_skipped_and_nothing_is_sent():
    async def scenario():
        rig, p = await proposed()
        rig.sim.events = [{"id": 9, "type": "route_disruption", "start_tick": 0, "end_tick": 3, "status": "ACTIVE", "parameters": {"route_ids": ["route-gazipur-mirpur"]}}]
        return await rig.executor.approve(p["id"]), rig

    out, rig = run(scenario())
    assert out["status"] == "FAILED" and out["lines"][0]["status"] == "SKIPPED" and out["lines"][0]["failure_code"] == "ROUTE_DISRUPTED"
    assert rig.sim.posts == []
    assert "line.skipped" in rig.actions()


def test_a_409_is_recorded_without_retry_and_the_proposal_fails():
    async def scenario():
        rig, p = await proposed()
        rig.sim.post_mode = "reject"
        rig.sim.reject_code = "DISPATCH_CAPACITY_EXCEEDED"
        return await rig.executor.approve(p["id"]), rig

    out, rig = run(scenario())
    (line,) = out["lines"]
    assert line["status"] == "REJECTED" and line["failure_code"] == "DISPATCH_CAPACITY_EXCEEDED" and len(rig.sim.posts) == 1
    assert out["status"] == "FAILED"
    assert line["attempts"][-1]["http_status"] == 409


def test_a_lost_response_leaves_the_line_unknown_and_the_next_snapshot_finds_it_by_key():
    async def scenario():
        rig, p = await proposed()
        rig.sim.post_mode = "drop_response"
        out = await rig.executor.approve(p["id"])
        assert out["lines"][0]["status"] == "UNKNOWN" and out["status"] == "EXECUTING"
        assert len({post["idempotency_key"] for post in rig.sim.posts}) == 1 and len(rig.sim.posts) == 3  # same key and body on every attempt
        assert len({post["quantity"] for post in rig.sim.posts}) == 1
        await rig.poll()  # the allocation is in the simulator's list
        return rig.db.proposal(p["id"])

    final = run(scenario())
    assert final["lines"][0]["status"] == "SUBMITTED" and final["status"] == "EXECUTED"


def test_an_unknown_line_not_found_after_the_lookup_window_fails(monkeypatch):
    monkeypatch.setattr(executor_module, "LOOKUP_WINDOW_SECONDS", 0.0)

    async def scenario():
        rig, p = await proposed()
        rig.sim.post_mode = "fail"  # nothing was recorded
        out = await rig.executor.approve(p["id"])
        assert out["lines"][0]["status"] == "UNKNOWN"
        await asyncio.sleep(0.01)
        await rig.poll()
        return rig.db.proposal(p["id"])

    final = run(scenario())
    assert final["lines"][0]["status"] == "FAILED" and final["lines"][0]["failure_code"] == "NOT_FOUND_AFTER_LOOKUP" and final["status"] == "FAILED"


def test_a_stale_snapshot_holds_the_approval_until_overridden_or_the_gate_reopens():
    async def scenario():
        rig, p = await proposed()
        rig.sim.stale = True
        held = await rig.executor.approve(p["id"])
        assert held["status"] == "HELD" and "degraded" in held["status_reason"] and rig.sim.posts == []
        await rig.executor.after_poll()  # still closed: nothing happens, and the same tick must not stop later retries
        assert rig.db.proposal(p["id"])["status"] == "HELD"
        # the gate reopens by itself: the approval stands and runs again
        rig.sim.stale = False
        await rig.poll()
        await rig.executor.after_poll()
        return rig, rig.db.proposal(p["id"])

    rig, final = run(scenario())
    assert final["status"] == "EXECUTED" and len(rig.sim.posts) == 1
    assert "proposal.held" in rig.actions() and "proposal.resumed" in rig.actions()


def test_the_override_lets_a_degraded_snapshot_execute_and_is_audited():
    async def scenario():
        rig, p = await proposed()
        rig.sim.stale = True
        return await rig.executor.approve(p["id"], override_stale=True), rig

    out, rig = run(scenario())
    assert out["status"] == "EXECUTED" and "gate.override" in rig.actions()


def test_no_override_helps_when_the_write_breaker_is_open():
    async def scenario():
        rig, p = await proposed()
        for _ in range(6):
            rig.client._breakers["write"].record_failure()  # noqa: SLF001
        assert rig.client.breaker_states()["write"]["state"] == "OPEN"
        return await rig.executor.approve(p["id"], override_stale=True), rig

    out, rig = run(scenario())
    assert out["status"] == "HELD" and "breaker" in out["status_reason"] and rig.sim.posts == []


def test_an_expired_proposal_is_not_executed():
    async def scenario():
        rig, p = await proposed()
        rig.sim.tick = p["expires_tick"] + 1
        return await rig.executor.approve(p["id"]), rig

    out, rig = run(scenario())
    assert out["status"] == "EXPIRED" and rig.sim.posts == []


def test_a_world_reset_fails_what_was_in_flight_in_the_old_world():
    async def scenario():
        rig, p = await proposed(ExecSim(tick=3))
        await rig.executor.approve(p["id"])
        assert rig.db.proposal(p["id"])["lines"][0]["status"] == "SUBMITTED"
        rig.sim.tick = 0
        rig.sim.allocations.clear()
        await rig.poll()
        return rig.world.resets_total, rig.db.proposal(p["id"])

    resets, old = run(scenario())
    assert resets == 1 and old["lines"][0]["status"] == "FAILED" and old["lines"][0]["failure_code"] == "WORLD_RESET"


def test_submitted_lines_follow_the_simulators_allocation_status():
    async def scenario():
        rig, p = await proposed()
        await rig.executor.approve(p["id"])
        key = f"fw-p{p['id']}-l{p['lines'][0]['id']}"
        rig.sim.allocations[key]["status"] = "IN_TRANSIT"
        rig.sim.tick = 1
        await rig.poll()
        mid = rig.db.proposal(p["id"])["lines"][0]["status"]
        rig.sim.allocations[key]["status"] = "ARRIVED"
        rig.sim.tick = 3
        await rig.poll()
        return mid, rig.db.proposal(p["id"])["lines"][0]["status"]

    assert run(scenario()) == ("IN_TRANSIT", "ARRIVED")


# -- API and operator token -------------------------------------------------------------


def make_api(token: str):
    sim = ExecSim()
    settings = Settings(poll_interval=0.01, ready_max_age=60, database_path=":memory:", operator_token=token)
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    return sim, TestClient(create_app(settings=settings, client=client))


def _first_diesel(api) -> dict:
    end = time.time() + 3
    while time.time() < end:
        found = [p for p in api.get("/api/proposals").json() if p["fuel_type"] == "DIESEL" and p["status"] == "PROPOSED"]
        if found:
            return found[0]
        time.sleep(0.02)
    raise AssertionError("no proposal")


def test_write_endpoints_need_the_operator_token():
    sim, app = make_api("s3cret")
    with app as api:
        p = _first_diesel(api)
        url = f"/api/proposals/{p['id']}/approve"
        assert api.post(url).status_code == 401
        assert api.post(url, headers={"X-Operator-Token": "wrong"}).json()["error"]["code"] == "UNAUTHORIZED"
        assert api.post(f"/api/proposals/{p['id']}/reject").status_code == 401
        assert sim.posts == []
        ok = api.post(url, headers={"X-Operator-Token": "s3cret"}, json={"override_stale": False})
        assert ok.status_code == 200 and ok.json()["status"] == "EXECUTED" and len(sim.posts) == 1
        again = api.post(url, headers={"X-Operator-Token": "s3cret"})
        assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"
        assert api.post("/api/proposals/9999/approve", headers={"X-Operator-Token": "s3cret"}).status_code == 404


def test_without_a_configured_token_every_write_is_refused():
    _, app = make_api("")
    with app as api:
        r = api.post("/api/proposals/1/approve", headers={"X-Operator-Token": ""})
        assert r.status_code == 503 and r.json()["error"]["code"] == "OPERATOR_TOKEN_NOT_SET"
