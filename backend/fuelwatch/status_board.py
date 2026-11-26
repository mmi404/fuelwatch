"""The component health board behind GET /api/health/components.

Every state is read from the running system when asked: nothing is cached from a good moment or set by
hand. The latency and error figures come from our own API's Prometheus histogram. They cover a recent
window, measured between successive calls to the board; until a full window has passed they cover the
time since the process started, and the response says which.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .health import Mode

HEALTHY, DEGRADED, DOWN, UNKNOWN = "HEALTHY", "DEGRADED", "DOWN", "UNKNOWN"
ROUTE = "/api/health/components"  # left out of the figures, so that the board does not measure itself
WINDOW_SECONDS = 300.0
_SAMPLE_EVERY = 5.0  # bounds the number of samples kept
DB_TIMEOUT = 2.0
ERROR_RATE_LIMIT = 0.05  # the API is DEGRADED from this share of 5xx answers
ERROR_RATE_MIN_REQUESTS = 20  # fewer requests than this are too few to judge by share


def _item(id_: str, name: str, state: str, detail: str) -> dict[str, str]:
    return {"id": id_, "name": name, "state": state, "detail": detail}


# -- our own HTTP figures ---------------------------------------------------------------


@dataclass(frozen=True)
class HttpTotals:
    """Cumulative counts since start: requests, 5xx answers and the latency histogram's buckets."""

    requests: float
    errors: float
    buckets: dict[float, float]  # upper bound in seconds to cumulative count


def read_totals(histogram: Any, skip_route: str = ROUTE) -> HttpTotals:
    requests = errors = 0.0
    buckets: dict[float, float] = {}
    for family in histogram.collect():
        for s in family.samples:
            if s.labels.get("route") == skip_route:
                continue
            if s.name.endswith("_bucket"):
                bound = float(s.labels["le"])
                buckets[bound] = buckets.get(bound, 0.0) + s.value
            elif s.name.endswith("_count"):
                requests += s.value
                if s.labels.get("status_class") == "5xx":
                    errors += s.value
    return HttpTotals(requests, errors, buckets)


def quantile(q: float, buckets: dict[float, float]) -> float | None:
    """Like PromQL's histogram_quantile: linear within the bucket that holds the rank; None without samples.

    A rank that falls past the last finite bound is reported as that bound.
    """
    bounds = sorted(buckets)
    total = buckets[bounds[-1]] if bounds else 0.0
    if total <= 0:
        return None
    rank = q * total
    lower, below = 0.0, 0.0
    for bound in bounds:
        count = buckets[bound]
        if count >= rank:
            if math.isinf(bound):
                return lower
            return lower + (bound - lower) * (rank - below) / (count - below)
        lower, below = bound, count
    return lower


def resident_memory_bytes(statm: str = "/proc/self/statm") -> int | None:
    """Resident set size from /proc; None where there is no /proc (Windows, macOS)."""
    try:
        with open(statm, encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class Window:
    """Differences of cumulative counters over the recent past."""

    def __init__(self, seconds: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic, cpu_clock: Callable[[], float] = time.process_time) -> None:
        self._seconds, self._clock, self._cpu_clock = seconds, clock, cpu_clock
        self._from_start = (clock(), HttpTotals(0.0, 0.0, {}), cpu_clock())
        self._samples: deque[tuple[float, HttpTotals, float]] = deque([self._from_start])

    def measure(self, totals: HttpTotals) -> tuple[dict[str, Any], dict[str, Any]]:
        now, cpu = self._clock(), self._cpu_clock()
        # Keep one sample that is at least a full window old to measure from.
        while len(self._samples) > 1 and self._samples[1][0] <= now - self._seconds:
            self._samples.popleft()
        base = self._samples[0]
        if now - self._samples[-1][0] >= _SAMPLE_EVERY:
            self._samples.append((now, totals, cpu))
        elapsed = now - base[0]
        requests = totals.requests - base[1].requests
        p95 = quantile(0.95, {le: n - base[1].buckets.get(le, 0.0) for le, n in totals.buckets.items()})
        http = {
            "p95_latency_ms": None if p95 is None else round(p95 * 1000, 1),
            "error_rate": (totals.errors - base[1].errors) / requests if requests > 0 else None,
            "requests": int(requests),
            "window": "since_start" if base is self._from_start else "recent",
            "window_seconds": round(elapsed, 1),
        }
        process = {
            "resident_memory_bytes": resident_memory_bytes(),
            "cpu_seconds_total": round(cpu, 2),
            "cpu_percent": round(100 * (cpu - base[2]) / elapsed, 1) if elapsed >= 1 else None,  # of one core, over the same window
        }
        return http, process


# -- components -------------------------------------------------------------------------


def api_item(http: dict[str, Any]) -> dict[str, str]:
    rate, n = http["error_rate"], http["requests"]
    if rate is not None and n >= ERROR_RATE_MIN_REQUESTS and rate >= ERROR_RATE_LIMIT:
        return _item("api", "Backend API", DEGRADED, f"Answering, but {rate:.1%} of {n} measured requests ended in a server error.")
    return _item("api", "Backend API", HEALTHY, "Answering requests.")


async def database_item(db: Any, timeout: float = DB_TIMEOUT) -> dict[str, str]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.to_thread(db.ping), timeout)
    except TimeoutError:
        return _item("database", "Database", DOWN, f"SQLite did not answer within {timeout:g} s.")
    except Exception as exc:  # noqa: BLE001 - any failure to read means the database is not usable
        return _item("database", "Database", DOWN, f"The SQLite check failed ({type(exc).__name__}).")
    return _item("database", "Database", HEALTHY, f"SQLite answered a read in {(time.perf_counter() - started) * 1000:.1f} ms.")


def simulator_item(snap: Any, mode: Mode, failures: int, breakers: dict[str, dict[str, Any]]) -> dict[str, str]:
    def item(state: str, detail: str) -> dict[str, str]:
        return _item("simulator", "Fuel Simulator", state, detail)

    read, write = breakers["read"]["state"], breakers["write"]["state"]
    if snap is None:
        if failures or read != "CLOSED":
            return item(DOWN, f"No successful read yet; {failures} reads in a row have failed.")
        return item(UNKNOWN, "Waiting for the first read.")
    age = f"{snap.age_seconds():.0f} s"
    if read == "OPEN":
        return item(DOWN, f"The read circuit breaker is open. The last good data is {age} old.")
    if mode is Mode.STALE:
        return item(DOWN, f"No fresh data: the last good read is {age} old.")
    problems = []
    if failures:
        problems.append(f"{failures} reads in a row have failed")
    if read != "CLOSED":
        problems.append("the read circuit breaker is testing recovery")
    if write != "CLOSED":
        problems.append("the write circuit breaker is not closed")
    if snap.stale:
        problems.append("the simulator flagged its data as stale")
    if not snap.consistent:
        problems.append("the tick moved during the last read")
    if problems:
        text = "; ".join(problems)
        return item(DEGRADED, f"{text[0].upper()}{text[1:]}. Last good read {age} ago.")
    return item(HEALTHY, f"Last good read {age} ago, tick {snap.tick}.")


def forecast_item(assessments: Any, assessed_tick: int | None, failed_stage: str | None) -> dict[str, str]:
    """The primary model is the one learned from demand history. A pair with no history yet runs on the starting profile."""

    def item(state: str, detail: str) -> dict[str, str]:
        return _item("forecast", "Prediction Service", state, detail)

    if assessed_tick is None:
        if failed_stage == "forecast":
            return item(DOWN, "The forecast update failed and there is no earlier result.")
        return item(UNKNOWN, "No forecast has been made yet.")
    if failed_stage == "forecast":
        return item(DEGRADED, f"The latest update failed. Showing the result from tick {assessed_tick}.")
    total = len(assessments)
    missing = sum(1 for a in assessments if a.level == "UNKNOWN")
    prior = sum(1 for a in assessments if a.confidence == "prior")
    low = sum(1 for a in assessments if a.confidence == "low")
    if missing == total:
        return item(DOWN, "No station and fuel pair has a forecast.")
    if missing or prior:
        parts = []
        if prior:
            parts.append(f"{prior} of {total} pairs use the starting profile because they have no demand history")
        if missing:
            parts.append(f"{missing} of {total} pairs have no forecast")
        return item(DEGRADED, f"Fallback in use: {'; '.join(parts)}. Tick {assessed_tick}.")
    short = f" {low} of them have under a day of history." if low else ""
    return item(HEALTHY, f"Model learned from demand history covers all {total} pairs at tick {assessed_tick}.{short}")


def decision_item(plan: Any, failed_stage: str | None) -> dict[str, str]:
    def item(state: str, detail: str) -> dict[str, str]:
        return _item("decision", "Decision Engine", state, detail)

    if failed_stage == "plan":
        if plan is None:
            return item(DOWN, "Planning failed and there is no earlier plan.")
        return item(DEGRADED, f"The latest planning step failed. The last plan is from tick {plan.tick} ({plan.policy} allocator).")
    if plan is None:
        return item(UNKNOWN, "No plan has been made yet.")
    if failed_stage == "forecast":
        return item(DEGRADED, f"Not planning while the forecast update fails. The last plan is from tick {plan.tick}.")
    if plan.fallback_from:
        return item(DEGRADED, f"Fallback in use: the {plan.fallback_from} allocator failed, so {plan.policy} made the plan at tick {plan.tick}.")
    return item(HEALTHY, f"The {plan.policy} allocator made the last plan, at tick {plan.tick}.")


def stream_item(enabled: bool, state: str) -> dict[str, str]:
    def item(health: str, detail: str) -> dict[str, str]:
        return _item("stream", "Tick Stream", health, detail)

    if not enabled:
        return item(UNKNOWN, "Turned off in the configuration. The backend polls instead.")
    if state == "CONNECTED":
        return item(HEALTHY, "Connected.")
    if state == "CONNECTING":
        return item(UNKNOWN, "Connecting. The backend polls meanwhile.")
    return item(DEGRADED, f"{state.capitalize()}. The backend still polls, so updates can be slower.")


def gate_item(has_snapshot: bool, reason: str | None) -> dict[str, str]:
    def item(state: str, detail: str) -> dict[str, str]:
        return _item("execution", "Execution Gate", state, detail)

    if not has_snapshot:
        return item(UNKNOWN, "Closed until the first read from the simulator.")
    if reason is None:
        return item(HEALTHY, "Open: approved shipments can be sent.")
    return item(DEGRADED, f"Closed, so shipments are held: {reason}.")


def overall_state(components: list[dict[str, str]]) -> str:
    """The worst state. An unknown tick stream alone does not count: it is only an early-refresh hint."""
    states = {c["state"] for c in components}
    for state in (DOWN, DEGRADED):
        if state in states:
            return state
    return UNKNOWN if any(c["state"] == UNKNOWN and c["id"] != "stream" for c in components) else HEALTHY


class StatusBoard:
    def __init__(self, *, db: Any, store: Any, client: Any, intel: Any, listener: Any, executor: Any, obs: Any, settings: Any, mode_fn: Callable[[], Mode]) -> None:
        self._db, self._store, self._client, self._intel = db, store, client, intel
        self._listener, self._executor, self._obs, self._settings, self._mode = listener, executor, obs, settings, mode_fn
        self._window = Window()

    async def report(self) -> dict[str, Any]:
        database = await database_item(self._db)
        http, process = self._window.measure(read_totals(self._obs._http))  # noqa: SLF001
        snap, intel = self._store.current, self._intel
        components = [
            api_item(http),
            database,
            simulator_item(snap, self._mode(), self._store.consecutive_failures, self._client.breaker_states()),
            forecast_item(intel.assessments, intel.assessed_tick, intel.failed_stage),
            decision_item(intel.plan, intel.failed_stage),
            stream_item(self._settings.stream_enabled, self._listener.state),
            gate_item(snap is not None, self._executor._gate_reason(snap, False)),  # noqa: SLF001
        ]
        return {"overall": overall_state(components), "checked_at": datetime.now(UTC).isoformat(), "components": components, "http": http, "process": process}
