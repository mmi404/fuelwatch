"""FastAPI application: serves the cached snapshot and health probes."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .client import SimulatorClient
from .db import Database
from .config import Settings
from .db import SCHEMA_VERSION
from .errors import SimulatorError, SimulatorUnavailable
from .events import EventBus, format_event, sse_stream
from .executor import ApiError, Executor
from .stream import StreamListener
from .health import Mode, compute_mode
from .intel import Intelligence
from . import logs
from .metrics import CONTENT_TYPE, HttpTimingMiddleware, Observability
from .poller import Poller
from .proposals import ProposalService
from .shed import LoadShedMiddleware
from .snapshot import SnapshotStore
from .status_board import StatusBoard
from .world import WorldTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_STATUS_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 422: "VALIDATION_ERROR", 503: "UNAVAILABLE"}


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    """The one error shape of this API (design part 7): {"error": {"code", "message"}}."""
    return {"error": {"code": code, "message": message}}


class ApproveBody(BaseModel):
    override_stale: bool = False


class AutopilotBody(BaseModel):
    """Only the fields sent are changed. Guard limits must be positive."""

    enabled: bool | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_liters_per_proposal: float | None = Field(default=None, gt=0)
    max_liters_per_tick: float | None = Field(default=None, gt=0)
    max_proposals_per_tick: int | None = Field(default=None, ge=1, le=100)


SIM_ACTIONS = ("run", "pause", "step", "reset")


def create_app(settings: Settings | None = None, client: SimulatorClient | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = SnapshotStore()
    owns_client = client is None
    client = client or SimulatorClient(settings.simulator_url, settings=settings)
    owns_db = db is None
    db = db or Database(settings.database_path)
    world = WorldTracker(db, client)
    proposals = ProposalService(db, world)
    intel = Intelligence(db, world, proposals=proposals, allocator=settings.allocator)
    executor = Executor(db, world, store, client, intel, settings, lambda: mode())
    poller = Poller(client, store, settings, world, intel, executor)
    executor.attach(poller)
    bus = EventBus()
    poller.bus = executor.bus = bus
    listener = StreamListener(client, store, poller)
    obs = Observability()
    for component in (client, poller, intel, proposals, executor, listener):
        component.obs = obs
    intel.forecaster.on_score = obs.forecast_score
    world.on_reset(lambda ev: obs.world_reset(ev.reason, ev.detail, ev.old_epoch_id, ev.new_epoch_id))
    obs.bind(client=client, store=store, world=world, intel=intel, listener=listener, mode_fn=lambda: mode())

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logs.configure(settings.log_json, settings.log_level)
        tasks = [asyncio.create_task(poller.run(), name="poller")]
        if settings.stream_enabled:
            tasks.append(asyncio.create_task(listener.run(), name="stream-listener"))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
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
    app.state.bus = bus
    app.state.obs = obs
    # Added first, so it runs inside the timing middleware: a refused request still gets a request id and is timed.
    app.add_middleware(LoadShedMiddleware, optional_limit=settings.shed_optional_limit, essential_limit=settings.shed_essential_limit, stream_limit=settings.shed_stream_limit, error_body=error_body, obs=obs)
    app.add_middleware(HttpTimingMiddleware, obs=obs)
    app.state.listener = listener
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

    board = StatusBoard(db=db, store=store, client=client, intel=intel, listener=listener, executor=executor, obs=obs, settings=settings, mode_fn=mode)

    @app.get("/api/health/components")
    async def api_health_components() -> dict[str, Any]:
        """Is FuelWatch itself healthy: each component's state, our API's latency and error rate, process use."""
        return await board.report()

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

    @app.get("/api/anomalies")
    async def api_anomalies() -> list[dict[str, Any]]:
        """Demand anomalies open now (design 6.5), unexplained first."""
        return intel.open_anomalies()

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

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(obs.exposition(), media_type=CONTENT_TYPE)

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        snap, epoch = store.current, world.epoch
        by_status: dict[str, int] = {}
        if epoch is not None:
            for p in await asyncio.to_thread(db.proposals, epoch_id=epoch.id, limit=1000):
                by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        info = meta()
        return {
            "mode": info["mode"],
            "tick": info["tick"],
            "tick_minutes": info["tick_minutes"],
            "age_seconds": info["age_seconds"],
            "epoch": info["epoch"],
            "resets_total": info["resets_total"],
            "breakers": info["breakers"],
            "retries": info["retries"],
            "history": info["history"],
            "forecast": info["forecast"],
            "stream": {**listener.info(), "enabled": settings.stream_enabled, "browser_subscribers": bus.subscribers, "events_published": bus.published, "events_dropped": bus.dropped},
            "execution": {**dict(executor.counters), "gate_open": executor._gate_reason(snap, False) is None},  # noqa: SLF001
            "autopilot": asdict(executor.autopilot),
            "proposals_by_status": by_status,
            "open_alerts": len(await asyncio.to_thread(proposals.alerts, ("open",))),
            "versions": {"app": app.version, "schema": SCHEMA_VERSION},
        }

    @app.get("/api/allocations")
    async def api_allocations(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Shipments we have sent or are sending, newest first, each with the simulator's own record when it is in the snapshot."""
        if not 1 <= limit <= 500:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "limit must be between 1 and 500"})
        epoch, snap = world.epoch, store.current
        if epoch is None:
            return []
        statuses = tuple(x.strip().upper() for x in status.split(",") if x.strip()) if status else None
        lines = await asyncio.to_thread(db.ledger, epoch.id, statuses=statuses, limit=limit)
        by_key = {a.idempotency_key: a for a in snap.allocations} if snap else {}
        for line in lines:
            found = by_key.get(line["idempotency_key"])
            line["simulator"] = found.model_dump(mode="json") if found else None
        return lines

    @app.get("/api/audit")
    async def api_audit(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500 or offset < 0:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "limit must be between 1 and 500 and offset 0 or more"})
        return await asyncio.to_thread(db.audit_rows, limit=limit, offset=offset)

    @app.get("/api/autopilot")
    async def api_autopilot() -> dict[str, Any]:
        return asdict(executor.autopilot)

    @app.put("/api/autopilot", dependencies=[Depends(require_operator)])
    async def api_set_autopilot(body: AutopilotBody) -> dict[str, Any]:
        changes = body.model_dump(exclude_none=True)
        return asdict(executor.set_autopilot(changes))

    @app.post("/api/sim/{action}", dependencies=[Depends(require_operator)])
    async def api_sim(action: str) -> dict[str, Any]:
        """Demo controls: the browser never calls the simulator, so its run, pause, step and reset go through here."""
        if action not in SIM_ACTIONS:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"unknown control {action!r}; use one of {', '.join(SIM_ACTIONS)}"})
        try:
            result = await client.admin_command(action)
        except SimulatorUnavailable as exc:
            raise HTTPException(status_code=503, detail={"code": "SIMULATOR_UNAVAILABLE", "message": str(exc)}) from exc
        except SimulatorError as exc:
            raise HTTPException(status_code=502, detail={"code": "SIMULATOR_ERROR", "message": str(exc)}) from exc
        await asyncio.to_thread(db.audit, "operator", f"sim.{action}", "simulator", "simulator", {})
        poller.wake()  # read the new state now
        return {"ok": True, "action": action, "simulator": result}

    @app.get("/api/stream")
    async def api_stream(request: Request) -> StreamingResponse:
        snap = store.current
        initial = [("snapshot", {"tick": snap.tick, "tick_minutes": snap.tick_minutes, "stale": snap.stale, "consistent": snap.consistent})] if snap else []
        return StreamingResponse(
            sse_stream(bus, initial=initial, disconnected=request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
