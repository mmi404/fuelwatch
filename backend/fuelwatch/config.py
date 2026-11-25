"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    simulator_url: str = "http://localhost:8000"
    # Per-request timeout towards the simulator, in seconds.
    request_timeout: float = 3.0
    # How often the poller asks the simulator for the current tick.
    poll_interval: float = 0.5
    # Re-read the whole world at least this often even if the tick did not move.
    refresh_max_age: float = 5.0
    # /readyz reports not ready when the snapshot is older than this many seconds.
    ready_max_age: float = 15.0
    # Attempts to obtain a snapshot whose tick did not move during the read.
    torn_read_attempts: int = 3

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            simulator_url=os.environ.get("SIMULATOR_URL", cls.simulator_url).rstrip("/"),
            request_timeout=_float("REQUEST_TIMEOUT", cls.request_timeout),
            poll_interval=_float("POLL_INTERVAL", cls.poll_interval),
            refresh_max_age=_float("REFRESH_MAX_AGE", cls.refresh_max_age),
            ready_max_age=_float("READY_MAX_AGE", cls.ready_max_age),
            torn_read_attempts=int(os.environ.get("TORN_READ_ATTEMPTS", cls.torn_read_attempts)),
        )
