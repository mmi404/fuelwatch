"""FastAPI application: serves the cached snapshot and health probes."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from .client import SimulatorClient
from .db import Database
from .config import Settings
from .executor import ApiError, Executor
from .health import Mode, compute_mode
from .intel import Intelligence
from .poller import Poller
from .proposals import ProposalService
from .snapshot import SnapshotStore
from .world import WorldTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_STATUS_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 422: "VALIDATION_ERROR", 503: "UNAVAILABLE"}


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    """The one error shape of this API (design part 7): {"error": {"code", "message"}}."""
    return {"error": {"code": code, "message": message}}


class ApproveBody(BaseModel):
    override_stale: bool = False


def create_app(settings: Settings | None = None, client: SimulatorClient | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = SnapshotStore()
    owns_client = client is None
    client = client or SimulatorClient(settings.simulator_url, settings=settings)
    owns_db = db is None
    db = db or Database(settings.database_path)
    world = WorldTracker(db, client)
    proposals = ProposalService(db, world)
    intel = Intelligence(db, world, proposals=proposals)
    executor = Executor(db, world, store, client, intel, settings, lambda: mode())
    poller = Poller(client, store, settings, world, intel, executor)
    executor.attach(poller)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(poller.run(), name="poller")
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if owns_client:
                await client.aclose()
            if owns_db:
                db.close()

    app = FastAPI(title="FuelWatch backend", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.poller = poller
    app.state.client = client
    app.state.world = world
    app.state.intel = intel
    app.state.executor = executor
    app.state.db = db

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            code, message = str(detail["code"]), str(detail.get("message") or "")
        else:
            code, message = _STATUS_CODES.get(exc.status_code, "ERROR"), str(detail)
        return JSONResponse(error_body(code, message), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        message = f"{where}: {first.get('msg', 'invalid request')}" if where else "invalid request"
        return JSONResponse(error_body("VALIDATION_ERROR", message), status_code=422)

    def mode() -> Mode:
        return compute_mode(
            store.current,
            consecutive_failures=store.consecutive_failures,
            any_breaker_not_closed=client.any_breaker_not_closed(),
            stale_after=settings.ready_max_age,
        )

    def meta() -> dict[str, Any]:
        snap = store.current
        return {
            "mode": mode().value,
            "has_snapshot": snap is not None,
            "tick": snap.tick if snap else None,
            "tick_minutes": snap.tick_minutes if snap else None,
            "age_seconds": snap.age_seconds() if snap else None,
            "simulator_stale_header": snap.stale if snap else None,
            "consistent_read": snap.consistent if snap else None,
            "consecutive_failures": store.consecutive_failures,
            "last_error": store.last_error,
            "breakers": client.breaker_states(),
            "retries": client.retry_stats(),
            **world.info(),
            "forecast": {**{k: v for k, v in intel.forecaster.accuracy().items() if k != "series"}, "assessed_tick": intel.assessed_tick, "error": intel.last_error},
        }

    @app.get("/livez")
    async def livez() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        info = meta()
        ready = info["mode"] in (Mode.LIVE.value, Mode.DEGRADED.value)
        return JSONResponse({"ready": ready, **info}, status_code=200 if ready else 503)

    @app.get("/api/snapshot")
    async def api_snapshot() -> dict[str, Any]:
        snap = store.current
        if snap is None:
            raise HTTPException(status_code=503, detail={"code": "NO_SNAPSHOT", "message": store.last_error or "no snapshot yet"})
        return {"meta": meta(), "world": snap.model_dump(mode="json")}

    @app.get("/api/risks")
    async def api_risks() -> list[dict[str, Any]]:
        """One row per station and fuel, most urgent first."""
        return intel.risks()

    @app.get("/api/forecast")
    async def api_forecast(station_id: str | None = None, fuel: str | None = None, ticks: int = 24) -> list[dict[str, Any]]:
        if not 1 <= ticks <= 96:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "ticks must be between 1 and 96"})
        return intel.forecast_rows(station_id, fuel, ticks)

    @app.get("/api/proposals")
    async def api_proposals(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Open and recent proposals of the current world, newest first, each with its explanation card."""
        if not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "limit must be between 1 and 200"})
        statuses = tuple(x.strip().upper() for x in status.split(",") if x.strip()) if status else None
        return await asyncio.to_thread(proposals.recent, statuses=statuses, limit=limit)

    @app.get("/api/proposals/{proposal_id}")
    async def api_proposal(proposal_id: int) -> dict[str, Any]:
        found = await asyncio.to_thread(proposals.get, proposal_id)
        if found is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"no proposal {proposal_id}"})
        return found

    def require_operator(x_operator_token: str | None = Header(default=None)) -> None:
        """Write and control endpoints need the shared secret; with none configured they are all refused."""
        if not settings.operator_token:
            raise HTTPException(status_code=503, detail={"code": "OPERATOR_TOKEN_NOT_SET", "message": "write endpoints are disabled until OPERATOR_TOKEN is set"})
        if x_operator_token is None or not hmac.compare_digest(x_operator_token.encode(), settings.operator_token.encode()):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "missing or wrong X-Operator-Token"})

    @app.post("/api/proposals/{proposal_id}/approve", dependencies=[Depends(require_operator)])
    async def api_approve(proposal_id: int, body: ApproveBody | None = None) -> dict[str, Any]:
        try:
            return await executor.approve(proposal_id, override_stale=bool(body and body.override_stale))
        except ApiError as exc:
            raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from exc

    @app.post("/api/proposals/{proposal_id}/reject", dependencies=[Depends(require_operator)])
    async def api_reject(proposal_id: int) -> dict[str, Any]:
        try:
            return await executor.reject(proposal_id)
        except ApiError as exc:
            raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from exc

    @app.get("/api/alerts")
    async def api_alerts(state: str | None = None) -> list[dict[str, Any]]:
        states = tuple(x.strip().lower() for x in state.split(",") if x.strip()) if state else None
        return await asyncio.to_thread(proposals.alerts, states)

    return app


_default_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """`uvicorn fuelwatch.main:app` builds the app on first use, so importing the module has no side effects."""
    global _default_app
    if name == "app":
        if _default_app is None:
            _default_app = create_app()
        return _default_app
    raise AttributeError(name)
