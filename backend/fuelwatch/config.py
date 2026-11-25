"""Runtime configuration, read from environment variables.

Every field can be overridden by the environment variable of the same name in
upper case, for example `POLL_INTERVAL=1.0`. Defaults follow the design (part 8.2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Settings:
    simulator_url: str = "http://localhost:8000"
    # Per-request timeout towards the simulator, in seconds.
    request_timeout: float = 3.0
    # How often the poller asks the simulator for the current tick.
    poll_interval: float = 0.5
    # Re-read the whole world at least this often even if the tick did not move.
    refresh_max_age: float = 5.0
    # Snapshots older than this many seconds are STALE and /readyz reports not ready.
    ready_max_age: float = 15.0
    # Attempts to obtain a snapshot whose tick did not move during the read.
    torn_read_attempts: int = 3
    # A whole world read that takes longer than this is abandoned: it would be too old to publish.
    world_deadline: float = 5.0

    # Retries: reads retry twice, writes (idempotent by key) up to three attempts in total.
    read_retries: int = 2
    read_backoff_base: float = 0.1
    read_backoff_cap: float = 1.0
    write_attempts: int = 3
    write_backoff_base: float = 0.2
    write_backoff_cap: float = 2.0
    # Retry budget: retries at most this fraction of requests in a rolling window,
    # with a floor so a quiet system can still retry.
    retry_budget_fraction: float = 0.4
    retry_budget_window: float = 30.0
    retry_budget_floor: int = 3

    # Most requests in flight to the simulator at once (it reset a connection at 50).
    max_in_flight: int = 8

    # Circuit breakers.
    breaker_consecutive_failures: int = 5
    breaker_failure_rate: float = 0.5
    breaker_min_calls: int = 10
    breaker_window: float = 30.0
    breaker_cooldown: float = 5.0
    breaker_max_cooldown: float = 30.0
    breaker_probes_needed: int = 2

    @classmethod
    def from_env(cls) -> Settings:
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = os.environ.get(f.name.upper())
            if raw is None:
                continue
            kind = type(f.default)
            values[f.name] = kind(raw)
        settings = cls(**values)  # type: ignore[arg-type]
        return cls(**{**values, "simulator_url": settings.simulator_url.rstrip("/")})  # type: ignore[arg-type]
