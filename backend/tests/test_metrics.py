import asyncio
import json
import logging
import re

import httpx
from fastapi.testclient import TestClient

from fuelwatch import logs
from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.main import create_app
from fuelwatch.metrics import Observability, endpoint_name

from .conftest import Sleeps
from .test_executor import ExecSim, Rig, _first_diesel, proposed, run

H = {"X-Operator-Token": "s3cret"}


def sample(text: str, name: str, **labels: str) -> float | None:
    """The value of one exposition line, or None."""
    for line in text.splitlines():
        if line.startswith("#") or not line.startswith(name):
            continue
        m = re.match(r"^(\w+)(?:\{(.*)\})?\s+(\S+)$", line)
        if not m or m.group(1) != name:
            continue
        got = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2) or ""))
        if all(got.get(k) == v for k, v in labels.items()):
            return float(m.group(3))
    return None


def make_api():
    sim = ExecSim()
    settings = Settings(poll_interval=0.01, ready_max_age=60, database_path=":memory:", operator_token="s3cret")
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    return sim, TestClient(create_app(settings=settings, client=client))


def test_metrics_endpoint_reports_the_catalogue_and_stays_within_the_series_budget():
    sim, app = make_api()
    with app as api:
        p = _first_diesel(api)
        api.post(f"/api/proposals/{p['id']}/approve", headers=H)
        api.get("/api/proposals")
        text = api.get("/metrics").text
        assert api.get("/metrics").headers["content-type"].startswith("text/plain")

    assert sample(text, "fuelwatch_simulator_requests_total", endpoint="/v1/instance", outcome="ok") >= 1
    assert sample(text, "fuelwatch_simulator_requests_total", endpoint="/v1/allocations", outcome="ok") >= 2  # reads and the POST
    assert sample(text, "fuelwatch_simulator_request_seconds_count", endpoint="/v1/instance") >= 1
    assert sample(text, "fuelwatch_breaker_state", breaker="read") == 0 and sample(text, "fuelwatch_breaker_state", breaker="write") == 0
    assert sample(text, "fuelwatch_mode", mode="LIVE") == 1 and sample(text, "fuelwatch_mode", mode="STALE") == 0
    assert sample(text, "fuelwatch_snapshot_tick") == 0 and sample(text, "fuelwatch_snapshot_age_seconds") is not None
    assert sample(text, "fuelwatch_hours_to_stockout", station="station-mirpur", fuel="DIESEL", quantile="p90") is not None
    assert sample(text, "fuelwatch_stream_state", state="DISCONNECTED") == 1
    assert sample(text, "fuelwatch_proposals_total", status="proposed") >= 1 and sample(text, "fuelwatch_proposals_total", status="approved") == 1
    assert sample(text, "fuelwatch_proposals_total", status="executed") == 1
    assert sample(text, "fuelwatch_decision_seconds_count", policy="lp") >= 1
    assert sample(text, "fuelwatch_http_request_seconds_count", route="/api/proposals/{proposal_id}/approve", method="POST", status_class="2xx") == 1
    assert sample(text, "fuelwatch_http_request_seconds_count", route="/api/proposals", method="GET", status_class="2xx") >= 1
    assert sample(text, "fuelwatch_http_request_seconds_count", route="/metrics") is None  # a scrape does not time itself
    series = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert len(series) < 500


def test_every_response_has_a_request_id_and_a_given_one_is_kept():
    _, app = make_api()
    with app as api:
        assert len(api.get("/livez").headers["x-request-id"]) >= 8
        assert api.get("/livez", headers={"X-Request-ID": "abc123"}).headers["x-request-id"] == "abc123"


def test_endpoint_names_replace_ids_so_labels_stay_bounded():
    assert endpoint_name("/v1/allocations/17/cancel") == "/v1/allocations/{id}/cancel"
    assert endpoint_name("/v1/stations") == "/v1/stations"


def test_request_outcomes_are_classified_by_final_result():
    async def scenario():
        obs = Observability()
        sim = ExecSim()
        client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
        client.obs = obs
        from fuelwatch.errors import SimulatorRejected, SimulatorUnavailable
        from fuelwatch.models import AllocationRequest

        await client.instance()
        sim.post_mode, sim.reject_code = "reject", "ROUTE_DISRUPTED"
        req = AllocationRequest(idempotency_key="k", source_depot_id="d", destination_station_id="s", route_id="r", fuel_type="DIESEL", quantity=10)
        try:
            await client.create_allocation(req)
        except SimulatorRejected:
            pass
        sim.post_mode = "fail"
        try:
            await client.create_allocation(req)
        except SimulatorUnavailable:
            pass
        return obs.exposition().decode(), client

    text, client = run(scenario())
    assert sample(text, "fuelwatch_simulator_requests_total", endpoint="/v1/instance", outcome="ok") == 1
    assert sample(text, "fuelwatch_simulator_requests_total", endpoint="/v1/allocations", outcome="rejected") == 1
    assert sample(text, "fuelwatch_simulator_requests_total", endpoint="/v1/allocations", outcome="transient") == 1
    assert client.retries_by_kind["write"] == 2  # three attempts on the failing write


def test_breaker_transitions_are_logged_as_events(caplog):
    async def scenario():
        obs = Observability()
        sim = ExecSim()
        sim.post_mode = "fail"
        client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
        client.obs = obs
        from fuelwatch.errors import SimulatorError
        from fuelwatch.models import AllocationRequest

        req = AllocationRequest(idempotency_key="k", source_depot_id="d", destination_station_id="s", route_id="r", fuel_type="DIESEL", quantity=10)
        for _ in range(6):
            try:
                await client.create_allocation(req)
            except SimulatorError:
                pass
        return obs

    with caplog.at_level(logging.INFO, logger="fuelwatch.obs"):
        run(scenario())
    opened = [r for r in caplog.records if getattr(r, "event", None) == "breaker.opened"]
    assert len(opened) == 1 and opened[0].fields["breaker"] == "write"


def test_json_logs_carry_the_fixed_fields_and_the_request_id():
    formatter = logs.JsonFormatter()
    logs.CONTEXT.update(tick=42, epoch=3)
    token = logs.request_id.set("req-1")
    try:
        record = logging.LogRecord("fuelwatch.obs", logging.WARNING, __file__, 1, "snapshot.read_failed", None, None)
        record.event, record.fields = "snapshot.read_failed", {"error": "boom", "consecutive": 2}
        line = json.loads(formatter.format(record))
        plain = json.loads(formatter.format(logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("there",), None)))
    finally:
        logs.request_id.reset(token)
        logs.CONTEXT.update(tick=None, epoch=None)
    assert line["event"] == "snapshot.read_failed" and line["level"] == "warning" and line["tick"] == 42 and line["epoch"] == 3
    assert line["request_id"] == "req-1" and line["error"] == "boom" and line["ts"].endswith("+00:00")
    assert plain["event"] == "log" and plain["msg"] == "hello there"


def test_only_events_from_the_fixed_list_can_be_emitted():
    import pytest

    with pytest.raises(AssertionError):
        logs.emit(logging.getLogger("x"), logging.INFO, "made.up.event")


def test_execution_and_proposal_events_are_logged_and_counted(caplog):
    async def scenario():
        rig, p = await proposed()
        rig.executor.obs = rig.intel.obs = rig.intel._proposals.obs = Observability()
        await rig.executor.approve(p["id"])
        return rig.executor.obs

    with caplog.at_level(logging.INFO, logger="fuelwatch.obs"):
        obs = run(scenario())
    events = [getattr(r, "event", None) for r in caplog.records]
    assert "execution.attempt" in events and "execution.outcome" in events
    text = obs.exposition().decode()
    assert sample(text, "fuelwatch_proposals_total", status="executed") == 1
