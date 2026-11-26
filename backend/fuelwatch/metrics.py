"""Prometheus metrics and the observability hooks (design 8.5).

`Observability` is the one object the rest of the code talks to: a component reports what happened
(`obs.request(...)`, `obs.proposal("EXECUTED")`) and this module decides how that becomes a metric and a
log line. State that already lives somewhere else (breakers, snapshot age, stream state, the risk
picture) is read at scrape time by a collector instead of being copied. Labels come from fixed sets, so
the number of series is bounded (checked by a test).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram, ProcessCollector, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from . import logs

log = logging.getLogger("fuelwatch.obs")
CONTENT_TYPE = CONTENT_TYPE_LATEST
_LATENCY = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_ID_SEGMENT = re.compile(r"/\d+(?=/|$)")
BREAKER_STATE = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}
STREAM_STATES = ("DISCONNECTED", "CONNECTING", "CONNECTED", "BACKOFF", "STALLED")
MODES = ("STARTING", "LIVE", "DEGRADED", "STALE")
ANOMALY_KINDS = ("explained", "unexplained")
SHED_PRIORITIES = ("essential", "optional", "stream")  # the classes of shed.py that can be refused
HORIZON_HOURS = 24.0  # reported when no stockout is projected within the 96-tick horizon


def endpoint_name(path: str) -> str:
    """A path with numeric ids replaced, so the label set stays fixed."""
    return _ID_SEGMENT.sub("/{id}", path)


class Observability:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self._requests = Counter("fuelwatch_simulator_requests_total", "Requests to the simulator by final outcome", ["endpoint", "outcome"], registry=r)
        self._request_seconds = Histogram("fuelwatch_simulator_request_seconds", "Simulator request latency including retries", ["endpoint"], buckets=_LATENCY, registry=r)
        self._inconsistent = Counter("fuelwatch_snapshot_inconsistent_total", "Snapshots read while the tick moved", registry=r)
        self._stale = Counter("fuelwatch_snapshot_stale_total", "Snapshots the simulator flagged stale", registry=r)
        self._resets = Counter("fuelwatch_world_resets_total", "World resets detected", registry=r)
        self._abs_pct = Histogram("fuelwatch_forecast_abs_pct_error", "Absolute percentage error of one-step demand forecasts", ["horizon"], buckets=(0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.25, 0.5, 1.0), registry=r)
        self._decision = Histogram("fuelwatch_decision_seconds", "Time to assess risk and plan shipments", ["policy"], buckets=_LATENCY, registry=r)
        self._fallbacks = Counter("fuelwatch_fallback_activations_total", "Steps down a degradation ladder", ["component", "from", "to"], registry=r)
        self._proposals = Counter("fuelwatch_proposals_total", "Proposals by the state they entered", ["status"], registry=r)
        self._outcomes = Counter("fuelwatch_allocation_outcomes_total", "Allocation outcomes", ["outcome"], registry=r)
        self._ledger_errors = Counter("fuelwatch_ledger_errors_total", "Ledger (SQLite) failures", registry=r)
        self._http = Histogram("fuelwatch_http_request_seconds", "Our own API latency", ["route", "method", "status_class"], buckets=_LATENCY, registry=r)
        self._shed = Counter("fuelwatch_requests_shed_total", "Requests to our API refused because of load", ["priority"], registry=r)
        self._bound: dict[str, Any] = {}
        r.register(_StateCollector(self))
        ProcessCollector(registry=r)  # process_cpu_seconds_total, process_resident_memory_bytes: from /proc, so Linux only

    def bind(self, **components: Any) -> None:
        """Give the scrape-time collector what it reads: store, client, world, intel, listener, executor, mode_fn."""
        self._bound.update(components)

    # -- events -------------------------------------------------------------------------

    def request(self, endpoint: str, outcome: str, seconds: float) -> None:
        self._requests.labels(endpoint, outcome).inc()
        self._request_seconds.labels(endpoint).observe(seconds)

    def breaker(self, kind: str, old: str, new: str) -> None:
        if new == "OPEN":
            logs.emit(log, logging.WARNING, "breaker.opened", breaker=kind, previous=old)
        elif new == "CLOSED":
            logs.emit(log, logging.INFO, "breaker.closed", breaker=kind, previous=old)

    def snapshot(self, snap: Any, epoch_id: int | None) -> None:
        logs.CONTEXT.update(tick=snap.tick, epoch=epoch_id)
        if not snap.consistent:
            self._inconsistent.inc()
            logs.emit(log, logging.WARNING, "snapshot.inconsistent", tick_minutes=snap.tick_minutes)
        if snap.stale:
            self._stale.inc()
        logs.emit(log, logging.INFO, "snapshot.published", stale=snap.stale, consistent=snap.consistent, allocations=len(snap.allocations))

    def snapshot_failed(self, error: str, consecutive: int) -> None:
        logs.emit(log, logging.WARNING, "snapshot.read_failed", error=error, consecutive=consecutive)

    def world_reset(self, reason: str, detail: str, old_epoch: int, new_epoch: int) -> None:
        self._resets.inc()
        logs.CONTEXT["epoch"] = new_epoch
        logs.emit(log, logging.WARNING, "world.reset", reason=reason, detail=detail, old_epoch=old_epoch, new_epoch=new_epoch)

    def forecast_score(self, abs_pct: float) -> None:
        self._abs_pct.labels("1").observe(abs_pct)

    def decision(self, policy: str, seconds: float) -> None:
        self._decision.labels(policy).observe(seconds)

    def fallback(self, component: str, old: str, new: str) -> None:
        self._fallbacks.labels(component, old, new).inc()
        logs.emit(log, logging.WARNING, f"{component}.fallback", **{"from": old, "to": new})

    def proposal(self, status: str, **fields: Any) -> None:
        self._proposals.labels(status.lower()).inc()
        event = {"PROPOSED": "proposal.created", "SUPERSEDED": "proposal.superseded", "EXPIRED": "proposal.expired"}.get(status)
        if event:
            logs.emit(log, logging.INFO, event, **fields)

    def allocation_outcome(self, outcome: str) -> None:
        self._outcomes.labels(outcome).inc()

    def execution(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        logs.emit(log, level, event, **fields)

    def ledger_error(self) -> None:
        self._ledger_errors.inc()

    def stream_stalled(self, **fields: Any) -> None:
        logs.emit(log, logging.WARNING, "stream.stalled", **fields)

    def http(self, route: str, method: str, status: int, seconds: float) -> None:
        self._http.labels(route, method, f"{status // 100}xx").observe(seconds)

    def shed(self, priority: str) -> None:
        assert priority in SHED_PRIORITIES, priority
        self._shed.labels(priority).inc()

    def exposition(self) -> bytes:
        return generate_latest(self.registry)


class _Noop:
    """Stands in for `Observability` in components that were built without one (unit tests, the bench)."""

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **k: None


NOOP: Any = _Noop()


class _StateCollector:
    """Reads state at scrape time instead of mirroring it on every change."""

    def __init__(self, obs: Observability) -> None:
        self._obs = obs

    def describe(self) -> list[Any]:
        return []

    def collect(self) -> Any:
        b = self._obs._bound  # noqa: SLF001
        client, store, world, intel, listener, mode_fn = (b.get(k) for k in ("client", "store", "world", "intel", "listener", "mode_fn"))

        if client is not None:
            g = GaugeMetricFamily("fuelwatch_breaker_state", "0 closed, 1 half-open, 2 open", labels=["breaker"])
            for name, info in client.breaker_states().items():
                g.add_metric([name], BREAKER_STATE.get(info["state"], 0))
            yield g
            c = CounterMetricFamily("fuelwatch_retries", "Retries by call class", labels=["kind"])
            for kind, n in client.retries_by_kind.items():
                c.add_metric([kind], n)
            yield c
            yield CounterMetricFamily("fuelwatch_retry_budget_exhausted", "Retries refused by the budget", value=client.retry_budget_exhausted_total)

        snap = store.current if store is not None else None
        age = GaugeMetricFamily("fuelwatch_snapshot_age_seconds", "Age of the current snapshot")
        tick = GaugeMetricFamily("fuelwatch_snapshot_tick", "Tick of the current snapshot")
        if snap is not None:
            age.add_metric([], snap.age_seconds())
            tick.add_metric([], snap.tick)
        yield age
        yield tick

        if mode_fn is not None:
            current = mode_fn().value
            g = GaugeMetricFamily("fuelwatch_mode", "1 for the active health mode", labels=["mode"])
            for m in MODES:
                g.add_metric([m], 1.0 if m == current else 0.0)
            yield g

        if listener is not None:
            g = GaugeMetricFamily("fuelwatch_stream_state", "1 for the current tick-stream state", labels=["state"])
            for s in STREAM_STATES:
                g.add_metric([s], 1.0 if s == listener.state else 0.0)
            yield g
            yield CounterMetricFamily("fuelwatch_stream_stalls", "Stream stalls detected", value=listener.stalls)

        if intel is not None:
            g = GaugeMetricFamily("fuelwatch_hours_to_stockout", f"Hours to stockout; {HORIZON_HOURS:g} when none within the horizon", labels=["station", "fuel", "quantile"])
            for a in intel.assessments:
                if a.level == "UNKNOWN":
                    continue
                g.add_metric([a.station_id, a.fuel_type, "p50"], a.hours_p50 if a.hours_p50 is not None else HORIZON_HOURS)
                g.add_metric([a.station_id, a.fuel_type, "p90"], a.hours_p90 if a.hours_p90 is not None else HORIZON_HOURS)
            yield g
            g = GaugeMetricFamily("fuelwatch_anomalies_open", "Stations with an open demand anomaly", labels=["kind"])
            for kind in ANOMALY_KINDS:
                g.add_metric([kind], sum(1 for a in intel.anomalies if a.kind == kind))
            yield g
            coverage = intel.forecaster.accuracy().get("coverage_80")
            g = GaugeMetricFamily("fuelwatch_forecast_interval_coverage", "Share of realised demand inside the 80% interval")
            if isinstance(coverage, float):
                g.add_metric([], coverage)
            yield g


class HttpTimingMiddleware:
    """Times our own API and gives every request an id that appears in its log lines and its response."""

    def __init__(self, app: Any, obs: Observability) -> None:
        self.app, self.obs = app, obs

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        rid = (headers.get(b"x-request-id") or b"").decode()[:64] or format(int(time.time_ns()) & 0xFFFFFFFFFFFF, "012x")
        token = logs.request_id.set(rid)
        started = time.perf_counter()
        status = {"code": 500}

        async def wrapped_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            if path != "/metrics" and not path.startswith("/api/stream"):  # a scrape or an endless stream would only distort the latency
                self.obs.http(path, scope["method"], status["code"], time.perf_counter() - started)
            logs.request_id.reset(token)
