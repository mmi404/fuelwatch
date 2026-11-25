"""The simulator's tick stream, used as an early-refresh hint (design 5.6).

It never affects correctness: the poller reads the world by REST either way. The listener only makes
the poller react within a tick instead of within a poll interval, and reports whether it can be
trusted. A connection that looks alive can be deaf (the simulator drops slow clients silently), so
liveness is judged by tick progress, never by connection state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .client import SimulatorClient
from .snapshot import SnapshotStore

if TYPE_CHECKING:
    from .poller import Poller

log = logging.getLogger("fuelwatch.stream")

STALL_SECONDS = 10.0  # the REST tick moved this long ago and no tick event came
BACKOFF_BASE, BACKOFF_CAP = 1.0, 30.0


class StreamListener:
    def __init__(
        self,
        client: SimulatorClient,
        store: SnapshotStore,
        poller: Poller,
        *,
        stall_seconds: float = STALL_SECONDS,
        read_timeout: float = 30.0,
        check_every: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client, self._store, self._poller = client, store, poller
        self._stall_seconds, self._read_timeout, self._clock, self._sleep = stall_seconds, read_timeout, clock, sleep
        self._check_every = check_every
        self.state = "DISCONNECTED"
        self.connects = 0
        self.stalls = 0
        self.tick_events = 0
        self.last_event_tick: int | None = None
        self.last_error: str | None = None
        self._rest_tick: int | None = None
        self._rest_changed_at = clock()
        self._last_tick_event_at = clock()

    def info(self) -> dict[str, object]:
        return {"state": self.state, "connects": self.connects, "stalls": self.stalls, "tick_events": self.tick_events, "last_event_tick": self.last_event_tick, "last_error": self.last_error}

    async def run(self) -> None:
        delay, was_connected = BACKOFF_BASE, False
        while True:
            self.state = "CONNECTING"
            outcome = "error"
            try:
                outcome = await self._session(was_connected)
                if outcome != "never_opened":
                    was_connected, delay = True, BACKOFF_BASE
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("stream session ended: %s", exc)
            if outcome == "stalled":
                continue  # STALLED goes straight back to CONNECTING
            self.state = "BACKOFF"
            await self._sleep(delay)
            delay = min(BACKOFF_CAP, delay * 2)

    async def _session(self, reconnect: bool) -> str:
        queue: asyncio.Queue[tuple[str, dict] | BaseException | None] = asyncio.Queue()

        async def reader() -> None:
            try:
                async for item in self._client.stream_events(self._read_timeout):
                    await queue.put(item)
                await queue.put(None)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001
                await queue.put(exc)

        task = asyncio.create_task(reader(), name="stream-reader")
        opened = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), self._check_every)
                except TimeoutError:
                    if opened and self._stalled():
                        self.state, self.stalls = "STALLED", self.stalls + 1
                        log.warning("stream stalled: the REST tick moved but no tick event arrived; reconnecting")
                        return "stalled"
                    continue
                if item is None:
                    self.state = "DISCONNECTED"
                    return "closed" if opened else "never_opened"
                if isinstance(item, BaseException):
                    raise item
                event, data = item
                if event == "open":
                    opened = True
                    self.state, self.connects = "CONNECTED", self.connects + 1
                    self._last_tick_event_at = self._clock()
                    if reconnect:  # the stream replays nothing, so read the world now
                        self._poller.wake()
                elif event == "simulation.tick":
                    self.tick_events += 1
                    self.last_event_tick = data.get("tick")
                    self._last_tick_event_at = self._clock()
                    self._poller.wake()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _stalled(self) -> bool:
        now, snap = self._clock(), self._store.current
        if snap is not None and snap.tick != self._rest_tick:
            self._rest_tick, self._rest_changed_at = snap.tick, now
        moved = snap is not None and (self.last_event_tick is None or snap.tick > self.last_event_tick)
        return moved and now - self._rest_changed_at > self._stall_seconds and now - self._last_tick_event_at > self._stall_seconds
