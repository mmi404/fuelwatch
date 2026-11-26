"""Async client for the BUP Fuel Supply Simulator.

Responsibilities: timeouts, normalising the simulator's two error shapes into
typed exceptions, surfacing the ``X-Simulator-Stale`` header, validating every
payload against the models, reading a whole consistent world, and the resilience
policy of design part 8.2:

* each failed request is retried (reads twice, idempotent writes up to three attempts)
  with full-jitter backoff, only for transient errors, within a retry budget;
* at most ``max_in_flight`` requests are outstanding at once;
* one circuit breaker for reads and one for writes, so a failing write path does not
  stop reads;
* a whole world read has a deadline.

This is the only module that talks to the simulator.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from .config import Settings
from .metrics import NOOP, endpoint_name
from .errors import (
    CircuitOpenError,
    MalformedResponse,
    SimulatorError,
    SimulatorInvalidRequest,
    SimulatorNotFound,
    SimulatorRejected,
    SimulatorUnavailable,
)
from .models import (
    Allocation,
    AllocationRequest,
    DemandRecord,
    Depot,
    Event,
    Instance,
    Metrics,
    Region,
    Route,
    Station,
    SupplyArrival,
)
from .resilience import BreakerState, CircuitBreaker, RetryBudget
from .snapshot import WorldSnapshot

T = TypeVar("T")

STALE_HEADER = "x-simulator-stale"

READ = "read"
WRITE = "write"


def _error_code_and_message(body: Any) -> tuple[str | None, str]:
    """Pull (code, message) out of either simulator error shape."""
    if isinstance(body, dict):
        for key in ("detail", "error"):
            inner = body.get(key)
            if isinstance(inner, dict):
                return inner.get("code"), str(inner.get("message") or inner)
            if isinstance(inner, str):
                return None, inner
            if isinstance(inner, list):  # FastAPI 422 validation detail
                return "VALIDATION_ERROR", str(inner)[:300]
    return None, "unrecognised error body"


def raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    try:
        body = response.json()
    except ValueError:
        body = None
    code, message = _error_code_and_message(body)
    if status == 404:
        raise SimulatorNotFound(message, status=status, code=code)
    if status == 409:
        raise SimulatorRejected(message, status=status, code=code)
    if status == 422:
        raise SimulatorInvalidRequest(message, status=status, code=code)
    if status >= 500:
        raise SimulatorUnavailable(message, status=status, code=code)
    raise SimulatorError(message, status=status, code=code)


class SimulatorClient:
    def __init__(
        self,
        base_url: str,
        *,
        settings: Settings | None = None,
        timeout: float | None = None,
        torn_read_attempts: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        s = settings or Settings()
        self._settings = s
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout if timeout is not None else s.request_timeout),
            transport=transport,
        )
        self._torn_read_attempts = max(1, torn_read_attempts if torn_read_attempts is not None else s.torn_read_attempts)
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._semaphore = asyncio.Semaphore(max(1, s.max_in_flight))
        self._budget = RetryBudget(s.retry_budget_fraction, s.retry_budget_window, s.retry_budget_floor, clock)
        self._breakers = {
            kind: CircuitBreaker(
                kind,
                consecutive_failures=s.breaker_consecutive_failures,
                failure_rate=s.breaker_failure_rate,
                min_calls=s.breaker_min_calls,
                window=s.breaker_window,
                cooldown=s.breaker_cooldown,
                max_cooldown=s.breaker_max_cooldown,
                probes_needed=s.breaker_probes_needed,
                clock=clock,
            )
            for kind in (READ, WRITE)
        }
        self.retries_total = 0
        self.retries_by_kind = {READ: 0, WRITE: 0}
        self.obs: Any = NOOP
        self.retry_budget_exhausted_total = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- introspection -------------------------------------------------------------

    def breaker_states(self) -> dict[str, dict[str, Any]]:
        return {name: b.info() for name, b in self._breakers.items()}

    def any_breaker_not_closed(self) -> bool:
        return any(b.state is not BreakerState.CLOSED for b in self._breakers.values())

    def retry_stats(self) -> dict[str, int]:
        return {**self._budget.stats(), "retries_total": self.retries_total, "budget_exhausted_total": self.retry_budget_exhausted_total}

    # -- low level -----------------------------------------------------------------

    async def _send_once(self, method: str, path: str, **kwargs: Any) -> tuple[Any, bool]:
        """One HTTP attempt. Raises SimulatorError (transient ones are SimulatorUnavailable)."""
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise SimulatorUnavailable(f"timeout calling {path}") from exc
        except httpx.HTTPError as exc:
            raise SimulatorUnavailable(f"network error calling {path}: {exc!r}") from exc
        raise_for_status(response)
        stale = response.headers.get(STALE_HEADER, "").lower() == "true"
        try:
            return response.json(), stale
        except ValueError as exc:
            self.obs.invalid_payload(endpoint_name(path))
            raise MalformedResponse(f"non-JSON body from {path}", status=response.status_code) from exc

    def _backoff(self, kind: str, attempt: int) -> float:
        """Full jitter: a uniform delay between zero and the capped exponential."""
        if kind == WRITE:
            base, cap = self._settings.write_backoff_base, self._settings.write_backoff_cap
        else:
            base, cap = self._settings.read_backoff_base, self._settings.read_backoff_cap
        return self._rng.uniform(0.0, min(cap, base * (2 ** (attempt - 1))))

    async def _send(self, method: str, path: str, *, kind: str = READ, retries: int | None = None, **kwargs: Any) -> tuple[Any, bool]:
        """Send with the resilience policy and report the final outcome, with its latency, to the observer."""
        breaker = self._breakers[kind]
        before, started, outcome = breaker.state.value, time.perf_counter(), "ok"
        try:
            return await self._send_policy(method, path, kind=kind, retries=retries, **kwargs)
        except CircuitOpenError:
            outcome = "circuit_open"
            raise
        except (SimulatorRejected, SimulatorNotFound):
            outcome = "rejected"
            raise
        except (SimulatorInvalidRequest, MalformedResponse):
            outcome = "invalid"
            raise
        except BaseException:
            outcome = "transient"
            raise
        finally:
            self.obs.request(endpoint_name(path), outcome, time.perf_counter() - started)
            if breaker.state.value != before:
                self.obs.breaker(kind, before, breaker.state.value)

    async def _send_policy(self, method: str, path: str, *, kind: str = READ, retries: int | None = None, **kwargs: Any) -> tuple[Any, bool]:
        """Send with the resilience policy. Returns (parsed JSON, stale flag)."""
        if retries is None:
            retries = self._settings.write_attempts - 1 if kind == WRITE else self._settings.read_retries
        breaker = self._breakers[kind]
        # The breaker judges a request by its final outcome, after its retries: retries already
        # absorb a single failed attempt, so counting attempts would trip it at the simulator's
        # ordinary 24% error rate. A request let through a non-closed breaker is a probe and is
        # not retried: it is there to find out whether the simulator is back.
        is_probe = breaker.state is not BreakerState.CLOSED
        if not breaker.allow():
            raise CircuitOpenError(f"{kind} circuit is open; {method} {path} not sent")
        if is_probe:
            retries = 0
        self._budget.record_request()
        attempt = 0
        recorded = False
        try:
            while True:
                try:
                    async with self._semaphore:
                        result = await self._send_once(method, path, **kwargs)
                except SimulatorUnavailable:
                    if attempt >= retries:
                        recorded = True
                        breaker.record_failure()
                        raise
                    if not self._budget.try_acquire_retry():
                        self.retry_budget_exhausted_total += 1
                        recorded = True
                        breaker.record_failure()
                        raise
                    attempt += 1
                    self.retries_total += 1
                    self.retries_by_kind[kind] += 1
                    await self._sleep(self._backoff(kind, attempt))
                    continue
                except SimulatorError:
                    # The simulator answered (a 409, 404, 422, or an unreadable body): it is healthy.
                    recorded = True
                    breaker.record_success()
                    raise
                recorded = True
                breaker.record_success()
                return result
        finally:
            if not recorded:
                # Cancelled (for example by the world deadline) or an unexpected error: a request
                # that never finished must not leave a half-open probe stuck.
                breaker.record_failure()

    def _parse(self, path: str, data: Any, tp: type[T] | Any) -> T:
        try:
            return TypeAdapter(tp).validate_python(data)
        except ValidationError as exc:
            # The request itself was already counted as ok: only here is it known that its content is unusable.
            self.obs.invalid_payload(endpoint_name(path))
            raise MalformedResponse(f"{path} did not match the expected shape: {exc.errors()[:2]}") from exc

    async def _get(self, path: str, tp: Any, **params: Any) -> tuple[Any, bool]:
        data, stale = await self._send("GET", path, params=params or None)
        return self._parse(path, data, tp), stale

    # -- single resources ----------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """The simulator's own liveness probe. It says alive, not usable, and bypasses retries and breakers."""
        data, _ = await self._send_once("GET", "/v1/health")
        return data

    async def instance(self) -> Instance:
        value, _ = await self._get("/v1/instance", Instance)
        return value

    async def demand_history(self, *, station_id: str | None = None, limit: int = 200) -> list[DemandRecord]:
        params: dict[str, Any] = {"limit": limit}
        if station_id:
            params["station_id"] = station_id
        value, _ = await self._get("/v1/demand-history", list[DemandRecord], **params)
        return value

    # -- demo controls and the tick stream --------------------------------------------------

    async def admin_command(self, name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run, pause, step, reset, add an event (`events`) or clear faults (`faults/clear`). One attempt:
        admin calls bypass injected faults and are not idempotent."""
        data, _ = await self._send_once("POST", f"/admin/{name}", **({"json": body} if body is not None else {}))
        return data if isinstance(data, dict) else {"result": data}

    async def stream_events(self, read_timeout: float = 30.0) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Server-sent events from `/v1/stream` as (event, data). Yields ("open", {}) once connected and
        ("keepalive", {}) for comment lines. Ends when the stream closes; raises on a refusal or a read
        timeout (no bytes for `read_timeout` seconds)."""
        timeout = httpx.Timeout(self._settings.request_timeout, read=read_timeout)
        async with httpx.AsyncClient(base_url=self._base_url, timeout=timeout, transport=self._transport) as http:
            try:
                async with http.stream("GET", "/v1/stream") as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise_for_status(response)
                    yield "open", {}
                    event, data = "message", []
                    async for line in response.aiter_lines():
                        if line == "":
                            if data:
                                try:
                                    yield event, json.loads(chr(10).join(data))
                                except ValueError:
                                    pass  # a malformed event is ignored: the stream is only a hint
                            event, data = "message", []
                        elif line.startswith(":"):
                            yield "keepalive", {}
                        elif line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
            except httpx.TimeoutException as exc:
                raise SimulatorUnavailable("no bytes from the stream within the read timeout") from exc
            except httpx.HTTPError as exc:
                raise SimulatorUnavailable(f"stream error: {exc!r}") from exc

    # -- writes --------------------------------------------------------------------

    async def create_allocation(self, request: AllocationRequest) -> Allocation:
        """POST an allocation. Safe to retry: the same key and body returns the existing allocation."""
        data, _ = await self._send("POST", "/v1/allocations", kind=WRITE, json=request.model_dump(mode="json"))
        return self._parse("/v1/allocations", data, Allocation)

    async def cancel_allocation(self, allocation_id: int) -> Allocation:
        """Cancel is not retried: a second cancel of a cancelled allocation is a 409, so an unknown outcome is ambiguous."""
        data, _ = await self._send("POST", f"/v1/allocations/{allocation_id}/cancel", kind=WRITE, retries=0)
        return self._parse("cancel", data, Allocation)

    # -- the whole world -----------------------------------------------------------

    async def read_world(self) -> WorldSnapshot:
        """Read every resource and return one snapshot, within the world deadline."""
        try:
            return await asyncio.wait_for(self._read_world(), timeout=self._settings.world_deadline)
        except TimeoutError as exc:
            raise SimulatorUnavailable(f"world read exceeded {self._settings.world_deadline:g} s") from exc

    async def _read_world(self) -> WorldSnapshot:
        """The tick is read before and after. If it moved, the read straddled a tick boundary
        (a torn read), so we try again. After the last attempt we keep the result but mark it
        ``consistent=False`` rather than fail.
        """
        last: WorldSnapshot | None = None
        for _ in range(self._torn_read_attempts):
            before = await self.instance()
            results = await asyncio.gather(
                self._get("/v1/regions", list[Region]),
                self._get("/v1/depots", list[Depot]),
                self._get("/v1/stations", list[Station]),
                self._get("/v1/routes", list[Route]),
                self._get("/v1/supply-arrivals", list[SupplyArrival]),
                self._get("/v1/events", list[Event]),
                self._get("/v1/allocations", list[Allocation]),
                self._get("/v1/metrics", Metrics),
                return_exceptions=True,
            )
            for item in results:
                if isinstance(item, BaseException):
                    raise item  # all reads have finished; none is left running behind our back
            reads: list[tuple[Any, bool]] = results  # type: ignore[assignment]
            after = await self.instance()
            consistent = before.tick == after.tick
            last = WorldSnapshot(
                instance=after,
                regions=tuple(reads[0][0]),
                depots=tuple(reads[1][0]),
                stations=tuple(reads[2][0]),
                routes=tuple(reads[3][0]),
                supply_arrivals=tuple(reads[4][0]),
                events=tuple(reads[5][0]),
                allocations=tuple(reads[6][0]),
                metrics=reads[7][0],
                fetched_at=datetime.now(UTC),
                stale=any(stale for _, stale in reads),
                consistent=consistent,
            )
            if consistent:
                return last
        assert last is not None
        return last
