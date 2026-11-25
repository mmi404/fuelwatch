"""FastAPI application: serves the cached snapshot and health probes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .client import SimulatorClient
from .config import Settings
from .poller import Poller
from .snapshot import SnapshotStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Settings | None = None, client: SimulatorClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = SnapshotStore()
    owns_client = client is None
    client = client or SimulatorClient(
        settings.simulator_url,
        timeout=settings.request_timeout,
        torn_read_attempts=settings.torn_read_attempts,
    )
    poller = Poller(client, store, settings)

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

    app = FastAPI(title="FuelWatch backend", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.poller = poller

    def meta() -> dict[str, Any]:
        snap = store.current
        age = snap.age_seconds() if snap else None
        return {
            "has_snapshot": snap is not None,
            "tick": snap.tick if snap else None,
            "tick_minutes": snap.tick_minutes if snap else None,
            "age_seconds": age,
            "simulator_stale_header": snap.stale if snap else None,
            "consistent_read": snap.consistent if snap else None,
            "consecutive_failures": store.consecutive_failures,
            "last_error": store.last_error,
            # Degraded: we are serving a snapshot while the simulator is failing us.
            "degraded": snap is not None and store.consecutive_failures > 0,
        }

    @app.get("/livez")
    async def livez() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        info = meta()
        if not info["has_snapshot"]:
            return JSONResponse({"ready": False, "reason": "no snapshot yet", **info}, status_code=503)
        if info["age_seconds"] > settings.ready_max_age:
            return JSONResponse({"ready": False, "reason": "snapshot too old", **info}, status_code=503)
        return JSONResponse({"ready": True, **info})

    @app.get("/api/snapshot")
    async def api_snapshot() -> dict[str, Any]:
        snap = store.current
        if snap is None:
            raise HTTPException(status_code=503, detail={"code": "NO_SNAPSHOT", "message": meta()["last_error"]})
        return {"meta": meta(), "world": snap.model_dump(mode="json")}

    return app


app = create_app()
