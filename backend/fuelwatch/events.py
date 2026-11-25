"""In-process event bus and the browser's server-sent events stream (design 7.1, `/api/stream`).

The bus is a hint channel: a slow browser loses old events rather than slowing the backend, and the
dashboard always has `/api/snapshot` and `/api/proposals` as the truth.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


class EventBus:
    def __init__(self, queue_size: int = 100) -> None:
        self._subscribers: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = set()
        self._queue_size = queue_size
        self.published = 0
        self.dropped = 0

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[tuple[str, dict[str, Any]]]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[tuple[str, dict[str, Any]]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        self.published += 1
        for queue in self._subscribers:
            if queue.full():  # the subscriber is slow: drop its oldest event, never block the publisher
                queue.get_nowait()
                self.dropped += 1
            queue.put_nowait((event, data))


def format_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def sse_stream(
    bus: EventBus,
    *,
    initial: list[tuple[str, dict[str, Any]]] | None = None,
    keepalive: float = 15.0,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Server-sent events: a comment first, the current state, then every event, with periodic keepalives."""
    queue = bus.subscribe()
    try:
        yield ": connected\n\n"
        for event, data in initial or []:
            yield format_event(event, data)
        while True:
            if disconnected is not None and await disconnected():
                return
            try:
                event, data = await asyncio.wait_for(queue.get(), keepalive)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield format_event(event, data)
    finally:
        bus.unsubscribe(queue)
