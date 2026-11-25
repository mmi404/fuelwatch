"""The single poller: one component reads the simulator, everyone else reads the snapshot.

Each cycle asks the simulator for the current tick (one cheap call) and only
re-reads the whole world when the tick moved, a refresh was requested, or the
snapshot is getting old. A failed read never replaces the last good snapshot;
it is recorded so the API can show the data is aging, and the loop backs off.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .client import SimulatorClient
from .config import Settings
from .errors import SimulatorError
from .snapshot import SnapshotStore
from .world import WorldTracker

if TYPE_CHECKING:
    from .intel import Intelligence

log = logging.getLogger("fuelwatch.poller")


class Poller:
    def __init__(self, client: SimulatorClient, store: SnapshotStore, settings: Settings, world: WorldTracker | None = None, intelligence: "Intelligence | None" = None) -> None:
        self._world = world
        self._intel = intelligence
        self._client = client
        self._store = store
        self._settings = settings
        self._force_refresh = True

    def request_refresh(self) -> None:
        """Ask for a full re-read on the next cycle, for example after we POST an allocation."""
        self._force_refresh = True

    async def poll_once(self) -> bool:
        """Run one cycle. Returns True if a new snapshot was published."""
        try:
            instance = await self._client.instance()
            current = self._store.current
            needs_refresh = (
                self._force_refresh
                or current is None
                or instance.tick != current.tick
                or instance.tick_minutes != current.tick_minutes
                or current.age_seconds() > self._settings.refresh_max_age
                or not current.consistent
            )
            if not needs_refresh:
                return False
            snapshot = await self._client.read_world()
            if self._world is not None:
                # Reset detection comes before publishing so nobody sees a new world under the old epoch.
                try:
                    self._world.observe(snapshot)
                except Exception:  # a failing database must not blind the dashboard
                    log.exception("epoch check failed")
            self._store.publish(snapshot)
            self._force_refresh = False
            if self._world is not None:
                try:
                    await self._world.sync_history(snapshot)
                except Exception:
                    log.exception("history sync failed")
            if self._intel is not None:
                await self._intel.update(snapshot)  # catches its own errors
            return True
        except SimulatorError as exc:
            self._store.record_failure(str(exc))
            log.warning("poll failed (%d in a row): %s", self._store.consecutive_failures, exc)
            return False

    def _delay(self) -> float:
        base = self._settings.poll_interval
        failures = self._store.consecutive_failures
        if failures == 0:
            return base
        return min(base * (2**failures), 5.0)

    async def run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self._delay())
