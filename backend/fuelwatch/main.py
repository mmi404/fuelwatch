"""FastAPI application: serves the cached snapshot and health probes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .client import SimulatorClient
from .db import Database
from .config import Settings
from .health import Mode, compute_mode
from .poller import Poller
from .snapshot import SnapshotStore
from .world import WorldTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_STATUS_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 422: "VALIDATION_ERROR", 503: "UNAVAILABLE"}


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    """The one error shape of this API (design part 7): {"error": {"code", "message"}}."""
    return {"error": {"code": code, "message": message}}


def create_app(settings: Settings | None = None, client: SimulatorClient | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = SnapshotStore()
    owns_client = client is None
    client = client or SimulatorClient(settings.simulator_url, settings=settings)
    owns_db = db is None
    db = db or Database(settings.database_path)
    world = WorldTracker(db, client)
    poller = Poller(client, store, settings, world)

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

    return app


app = create_app()
