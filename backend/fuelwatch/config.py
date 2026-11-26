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
    # Where world epochs and demand history are kept (":memory:" for tests).
    database_path: str = "data/fuelwatch.db"
    # Per-request timeout towards the simulator, in seconds.
    request_timeout: float = 3.0
    # How often the poller asks the simulator for the current tick.
    poll_interval: float = 0.5
    # Re-read the whole world at least this often even if the tick did not move.
    refresh_max_age: float = 5.0
    # Snapshots older than this many seconds are STALE and /readyz reports not ready.
    ready_max_age: float = 15.0
    # Shared secret for write and control endpoints (header X-Operator-Token). Empty means they are all refused.
    operator_token: str = ""
    # Listen to the simulator's tick stream as an early-refresh hint. Off in the dataclass so unit tests stay quiet;
    # `from_env` turns it on unless STREAM_ENABLED says otherwise.
    stream_enabled: bool = False
    # One JSON object per log line. Off in the dataclass so unit tests keep plain output; `from_env` turns it on.
    log_json: bool = False
    log_level: str = "INFO"
    # Allocation policy: "lp" (rolling-horizon linear programme, falling back to greedy then rules), "greedy" or "rules".
    allocator: str = "lp"
    # Attempts to obtain a snapshot whose tick did not move during the read.
    torn_read_attempts: int = 3
    # A whole world read that takes longer than this is abandoned: it would be too old to publish.
    world_deadline: float = 5.0

    # Load shedding of our own API (design 8.7): most requests in flight before a new one is answered 503. Optional reads
    # are refused first; probes, the metrics scrape and operator actions never are. A console tab has at most about 10
    # reads in flight, so 128 leaves room for 10 tabs asking at the same instant, with some to spare. 0 turns a limit off.
    shed_optional_limit: int = 128
    shed_essential_limit: int = 256
    # Most browser event streams open at once.
    shed_stream_limit: int = 200

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
    # A failed probe doubles the cool-down up to this cap. It equals the base by default, so it does not grow: the
    # console must be live again within about 10 s of a fault clearing (NFR-4), and with a 30 s cap it took up to
    # about 24 s in a drill. The cost is one probe every few seconds while the simulator is down.
    breaker_max_cooldown: float = 5.0
    breaker_probes_needed: int = 2

    @classmethod
    def from_env(cls) -> Settings:
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = os.environ.get(f.name.upper())
            if raw is None:
                continue
            kind = type(f.default)
            values[f.name] = raw.strip().lower() in ("1", "true", "yes", "on") if kind is bool else kind(raw)
        values.setdefault("stream_enabled", True)
        values.setdefault("log_json", True)
        settings = cls(**values)  # type: ignore[arg-type]
        return cls(**{**values, "simulator_url": settings.simulator_url.rstrip("/")})  # type: ignore[arg-type]
