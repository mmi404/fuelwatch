"""Async client for the BUP Fuel Supply Simulator.

Responsibilities: timeouts, normalising the simulator's two error shapes into
typed exceptions, surfacing the ``X-Simulator-Stale`` header, validating every
payload against the models, and reading a whole consistent world.

Retries and the circuit breaker are layered on top of this later; the client
only classifies errors (``SimulatorError.transient``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from .errors import (
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
from .snapshot import WorldSnapshot

T = TypeVar("T")

STALE_HEADER = "x-simulator-stale"


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
        timeout: float = 3.0,
        torn_read_attempts: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )
        self._torn_read_attempts = max(1, torn_read_attempts)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- low level -----------------------------------------------------------------

    async def _send(self, method: str, path: str, **kwargs: Any) -> tuple[Any, bool]:
        """Send a request; return (parsed JSON, stale flag). Raises SimulatorError."""
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
            raise MalformedResponse(f"non-JSON body from {path}", status=response.status_code) from exc

    @staticmethod
    def _parse(path: str, data: Any, tp: type[T] | Any) -> T:
        try:
            return TypeAdapter(tp).validate_python(data)
        except ValidationError as exc:
            raise MalformedResponse(f"{path} did not match the expected shape: {exc.errors()[:2]}") from exc

    async def _get(self, path: str, tp: Any, **params: Any) -> tuple[Any, bool]:
        data, stale = await self._send("GET", path, params=params or None)
        return self._parse(path, data, tp), stale

    # -- single resources ----------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        data, _ = await self._send("GET", "/v1/health")
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

    # -- writes --------------------------------------------------------------------

    async def create_allocation(self, request: AllocationRequest) -> Allocation:
        data, _ = await self._send("POST", "/v1/allocations", json=request.model_dump(mode="json"))
        return self._parse("/v1/allocations", data, Allocation)

    async def cancel_allocation(self, allocation_id: int) -> Allocation:
        data, _ = await self._send("POST", f"/v1/allocations/{allocation_id}/cancel")
        return self._parse("cancel", data, Allocation)

    # -- the whole world -----------------------------------------------------------

    async def read_world(self) -> WorldSnapshot:
        """Read every resource and return one snapshot.

        The tick is read before and after. If it moved, the read straddled a tick
        boundary (a torn read), so we try again. After the last attempt we keep
        the result but mark it ``consistent=False`` rather than fail.
        """
        last: WorldSnapshot | None = None
        for attempt in range(1, self._torn_read_attempts + 1):
            before = await self.instance()
            reads = await asyncio.gather(
                self._get("/v1/regions", list[Region]),
                self._get("/v1/depots", list[Depot]),
                self._get("/v1/stations", list[Station]),
                self._get("/v1/routes", list[Route]),
                self._get("/v1/supply-arrivals", list[SupplyArrival]),
                self._get("/v1/events", list[Event]),
                self._get("/v1/allocations", list[Allocation]),
                self._get("/v1/metrics", Metrics),
            )
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
