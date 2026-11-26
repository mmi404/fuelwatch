"""Load shedding for our own API (design part 8.7).

When too many requests are in flight, low-priority reads are refused at once with `503` and `Retry-After`, instead of
joining a queue that makes every answer late. One counter covers all sheddable reads and each class has its own ceiling,
so as load rises the optional reads are refused first and the essential ones keep the headroom above that. Health
probes, the metrics scrape and operator actions are never counted and never refused. The event stream lasts as long as
a browser tab, so it has a count of its own and does not use up request slots.

This is a plain ASGI middleware: Starlette's `BaseHTTPMiddleware` would get between a streaming response and its client.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from .metrics import NOOP

EXEMPT, ESSENTIAL, OPTIONAL, STREAM = "exempt", "essential", "optional", "stream"
RETRY_AFTER_SECONDS = 5  # the console's own poll interval

_PROBES = frozenset({"/livez", "/readyz", "/metrics"})
# The routes that require the operator token. Matched by method too: a GET of the same path is an ordinary read.
_OPERATOR = (
    ("POST", re.compile(r"/api/proposals/[^/]+/(approve|reject)")),
    ("PUT", re.compile(r"/api/autopilot")),
    ("POST", re.compile(r"/api/sim/[^/]+")),
)
# What the console loads together on every refresh. It applies the first five only if all of them answer, and it hides
# the anomaly section when that read fails, which would look like "nothing unusual".
_ESSENTIAL = frozenset({
    "/api/status", "/api/snapshot", "/api/risks", "/api/proposals", "/api/alerts", "/api/anomalies", "/api/delivery-checks",
    "/api/health/components",  # our own health: needed most when the backend is struggling
    "/api/me",  # who is signed in
})
_ONE_PROPOSAL = re.compile(r"/api/proposals/[^/]+")


def classify(method: str, path: str) -> str:
    """The priority class of a request. A route not named here is optional."""
    if path in _PROBES or any(method == m and pattern.fullmatch(path) for m, pattern in _OPERATOR):
        return EXEMPT
    if path == "/api/stream":
        return STREAM
    if path in _ESSENTIAL or _ONE_PROPOSAL.fullmatch(path):
        return ESSENTIAL
    return OPTIONAL


class LoadShedMiddleware:
    """A limit of 0 means no limit for that class."""

    def __init__(
        self,
        app: Any,
        *,
        optional_limit: int,
        essential_limit: int,
        stream_limit: int,
        error_body: Callable[[str, str], Any],
        obs: Any = NOOP,
    ) -> None:
        self.app, self.obs, self._error_body = app, obs, error_body
        self._limits = {OPTIONAL: optional_limit, ESSENTIAL: essential_limit, STREAM: stream_limit}
        self.in_flight = 0  # essential and optional requests together
        self.streams = 0

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = classify(scope["method"], scope["path"]) if scope["type"] == "http" else EXEMPT
        if kind == EXEMPT:
            await self.app(scope, receive, send)
            return
        stream = kind == STREAM
        limit = self._limits[kind]
        if limit > 0 and (self.streams if stream else self.in_flight) >= limit:
            await self._refuse(kind, send)
            return
        # No await between the check above and this count, so the limit cannot be overshot.
        if stream:
            self.streams += 1
        else:
            self.in_flight += 1
        try:
            if not stream:
                # Most reads are answered from memory without ever pausing, so a backlog of them would wait unseen in
                # the event loop. Pausing once here lets every request that has already arrived be counted first.
                await asyncio.sleep(0)
            await self.app(scope, receive, send)
        finally:  # also on an error, and on the cancellation that follows a client disconnect
            if stream:
                self.streams -= 1
            else:
                self.in_flight -= 1

    async def _refuse(self, kind: str, send: Any) -> None:
        self.obs.shed(kind)  # counted, not logged: a log line per refusal would add to the overload
        what = "too many live event streams are open" if kind == STREAM else "the server is busy"
        body = json.dumps(self._error_body("OVERLOADED", f"{what}; try again in {RETRY_AFTER_SECONDS} s")).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"retry-after", str(RETRY_AFTER_SECONDS).encode()),
            (b"cache-control", b"no-store"),
        ]
        await send({"type": "http.response.start", "status": 503, "headers": headers})
        await send({"type": "http.response.body", "body": body})
