"""Structured JSON logs (design 8.5): one object per line with a fixed set of event names."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# The fixed list of event names, so they can be searched and alerted on.
EVENTS = frozenset({
    "snapshot.published", "snapshot.read_failed", "snapshot.inconsistent", "breaker.opened", "breaker.closed", "world.reset",
    "forecast.fallback", "allocator.fallback", "proposal.created", "proposal.superseded", "proposal.expired",
    "execution.attempt", "execution.outcome", "execution.unknown_resolved", "stream.stalled",
})

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
CONTEXT: dict[str, Any] = {"tick": None, "epoch": None}  # updated by the poller; there is one world per process


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": event or "log",
            "tick": CONTEXT["tick"],
            "epoch": CONTEXT["epoch"],
            "request_id": request_id.get(),
        }
        if not event:
            out["logger"] = record.name
            out["msg"] = record.getMessage()
        out.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(json_logs: bool, level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger. Called once at server start, never by library code."""
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # one INFO line per simulator call drowns everything else


def emit(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    assert event in EVENTS, f"unknown log event {event}"
    logger.log(level, event, extra={"event": event, "fields": fields})
