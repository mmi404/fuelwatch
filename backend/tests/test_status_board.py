import asyncio
import math
from datetime import timedelta
from types import SimpleNamespace

from prometheus_client import ProcessCollector

from fuelwatch.db import Database
from fuelwatch.health import Mode
from fuelwatch.metrics import Observability
from fuelwatch.status_board import (
    ROUTE,
    HttpTotals,
    Window,
    api_item,
    database_item,
    decision_item,
    forecast_item,
    gate_item,
    overall_state,
    quantile,
    read_totals,
    resident_memory_bytes,
    simulator_item,
    stream_item,
)

from .test_metrics import make_api

CLOSED = {"read": {"state": "CLOSED"}, "write": {"state": "CLOSED"}}


def snapshot(age=2.0, stale=False, consistent=True):
    return SimpleNamespace(tick=7, stale=stale, consistent=consistent, age_seconds=lambda: timedelta(seconds=age).total_seconds())


def pairs(*confidences):
    return [SimpleNamespace(confidence=c, level="UNKNOWN" if c == "none" else "OK") for c in confidences]


def test_the_endpoint_reports_every_component_from_the_running_app():
    _, app = make_api()
    with app as api:
        for _ in range(200):  # wait for the first poll to publish and plan
            if app.app.state.intel.plan is not None:
                break
            api.get("/livez")
        first = api.get(ROUTE)
        body = api.get(ROUTE).json()

    assert first.status_code == 200
    states = {c["id"]: c["state"] for c in body["components"]}
    assert list(states) == ["api", "database", "simulator", "forecast", "decision", "stream", "execution"]
    assert states["api"] == states["database"] == states["simulator"] == states["decision"] == states["execution"] == "HEALTHY"
    assert states["stream"] == "UNKNOWN"  # turned off in the test settings
    assert all(c["name"] and c["detail"] for c in body["components"])
    assert body["overall"] in ("HEALTHY", "DEGRADED")
    assert body["http"]["window"] == "since_start" and body["http"]["requests"] >= 0
    assert body["process"]["cpu_seconds_total"] > 0


def test_a_closed_database_is_down():
    db = Database(":memory:")
    assert asyncio.run(database_item(db))["state"] == "HEALTHY"
    db.close()
    assert asyncio.run(database_item(db))["state"] == "DOWN"


def test_a_database_that_does_not_answer_is_down():
    import time

    item = asyncio.run(database_item(SimpleNamespace(ping=lambda: time.sleep(0.3)), timeout=0.05))
    assert item["state"] == "DOWN" and "did not answer" in item["detail"]


def test_simulator_states_follow_the_snapshot_and_breakers():
    assert simulator_item(None, Mode.STARTING, 0, CLOSED)["state"] == "UNKNOWN"
    assert simulator_item(None, Mode.STARTING, 3, CLOSED)["state"] == "DOWN"
    assert simulator_item(snapshot(), Mode.LIVE, 0, CLOSED)["state"] == "HEALTHY"
    assert simulator_item(snapshot(age=60), Mode.STALE, 9, CLOSED)["state"] == "DOWN"
    assert simulator_item(snapshot(), Mode.DEGRADED, 0, {**CLOSED, "read": {"state": "OPEN"}})["state"] == "DOWN"
    flagged = simulator_item(snapshot(stale=True), Mode.DEGRADED, 2, CLOSED)
    assert flagged["state"] == "DEGRADED" and "2 reads in a row" in flagged["detail"] and "stale" in flagged["detail"]


def test_forecast_says_when_it_runs_on_the_fallback_prior():
    assert forecast_item((), None, None)["state"] == "UNKNOWN"
    assert forecast_item((), None, "forecast")["state"] == "DOWN"
    assert forecast_item(pairs("normal", "low"), 5, None)["state"] == "HEALTHY"
    prior = forecast_item(pairs("normal", "prior"), 5, None)
    assert prior["state"] == "DEGRADED" and "1 of 2" in prior["detail"]
    assert forecast_item(pairs("none", "none"), 5, None)["state"] == "DOWN"
    assert forecast_item(pairs("normal"), 5, "forecast")["state"] == "DEGRADED"
    assert forecast_item(pairs("normal"), 5, "plan")["state"] == "HEALTHY"


def test_decision_engine_names_the_policy_and_any_fallback():
    plan = SimpleNamespace(tick=4, policy="lp", fallback_from=None)
    assert decision_item(None, None)["state"] == "UNKNOWN"
    assert decision_item(None, "plan")["state"] == "DOWN"
    healthy = decision_item(plan, None)
    assert healthy["state"] == "HEALTHY" and "lp" in healthy["detail"]
    fallback = decision_item(SimpleNamespace(tick=4, policy="greedy", fallback_from="lp"), None)
    assert fallback["state"] == "DEGRADED" and "lp allocator failed" in fallback["detail"]
    assert decision_item(plan, "plan")["state"] == decision_item(plan, "forecast")["state"] == "DEGRADED"


def test_stream_gate_and_overall_states():
    assert stream_item(False, "DISCONNECTED")["state"] == "UNKNOWN"
    assert stream_item(True, "CONNECTED")["state"] == "HEALTHY"
    assert stream_item(True, "BACKOFF")["state"] == "DEGRADED"
    assert gate_item(False, "no snapshot yet")["state"] == "UNKNOWN"
    assert gate_item(True, None)["state"] == "HEALTHY"
    assert gate_item(True, "data is 20 s old")["state"] == "DEGRADED"
    ok = {"id": "api", "state": "HEALTHY"}
    assert overall_state([ok, {"id": "stream", "state": "UNKNOWN"}]) == "HEALTHY"
    assert overall_state([ok, {"id": "forecast", "state": "UNKNOWN"}]) == "UNKNOWN"
    assert overall_state([ok, {"id": "gate", "state": "DEGRADED"}, {"id": "database", "state": "DOWN"}]) == "DOWN"


def test_p95_comes_from_the_histogram_and_leaves_out_the_board_itself():
    obs = Observability()
    assert read_totals(obs._http).requests == 0 and quantile(0.95, read_totals(obs._http).buckets) is None
    for _ in range(99):
        obs.http("/api/status", "GET", 200, 0.02)
    obs.http("/api/status", "GET", 503, 0.02)
    obs.http(ROUTE, "GET", 500, 4.0)
    totals = read_totals(obs._http)
    assert totals.requests == 100 and totals.errors == 1
    assert 0.01 < quantile(0.95, totals.buckets) <= 0.025
    assert quantile(0.95, {1.0: 0.0, 5.0: 0.0, math.inf: 3.0}) == 5.0  # past the last finite bound


def test_the_window_starts_at_process_start_and_then_forgets_old_requests():
    now, cpu = [0.0], [0.0]
    window = Window(seconds=60, clock=lambda: now[0], cpu_clock=lambda: cpu[0])
    nothing, _ = window.measure(HttpTotals(0, 0, {}))
    assert nothing["p95_latency_ms"] is None and nothing["error_rate"] is None

    now[0], cpu[0] = 10.0, 1.0
    early, process = window.measure(HttpTotals(100, 50, {0.1: 100, math.inf: 100}))
    assert early["window"] == "since_start" and early["error_rate"] == 0.5 and process["cpu_percent"] == 10.0
    assert api_item(early)["state"] == "DEGRADED"

    now[0] = 100.0
    later, _ = window.measure(HttpTotals(140, 50, {0.1: 140, math.inf: 140}))
    assert later["window"] == "recent" and later["window_seconds"] == 90.0
    assert later["requests"] == 40 and later["error_rate"] == 0.0 and api_item(later)["state"] == "HEALTHY"


def test_memory_is_null_without_proc_and_the_collector_is_on_our_registry(tmp_path):
    assert resident_memory_bytes(str(tmp_path / "missing")) is None
    text = Observability().exposition().decode()
    expected = [f.name for f in ProcessCollector(registry=None).collect()]  # empty without /proc
    assert all(name in text for name in expected)
