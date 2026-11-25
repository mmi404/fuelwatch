"""Retry budget and circuit breaker (design part 8.2 and part 5.3).

Both take an injectable clock so their behaviour can be tested without waiting.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from enum import StrEnum
from typing import Any

Clock = Callable[[], float]


class BreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class RetryBudget:
    """Allows retries up to a fraction of recent requests, so a retry storm cannot start.

    A retry is allowed while the retries made in the rolling window stay below
    `max(floor, fraction x requests in the window)`. The floor lets a quiet system
    still retry a request or two. At the simulator's default error fault (24% of
    requests fail) about 31% extra requests are needed, so the default fraction of
    0.4 leaves room, while a real outage (nearly every request failing) is cut off.
    """

    def __init__(self, fraction: float = 0.4, window: float = 30.0, floor: int = 3, clock: Clock = time.monotonic) -> None:
        self._fraction = fraction
        self._window = window
        self._floor = floor
        self._clock = clock
        self._requests: deque[float] = deque()
        self._retries: deque[float] = deque()

    def _trim(self, now: float) -> None:
        cutoff = now - self._window
        for q in (self._requests, self._retries):
            while q and q[0] < cutoff:
                q.popleft()

    def record_request(self) -> None:
        now = self._clock()
        self._trim(now)
        self._requests.append(now)

    def try_acquire_retry(self) -> bool:
        now = self._clock()
        self._trim(now)
        allowed = max(self._floor, self._fraction * len(self._requests))
        if len(self._retries) < allowed:
            self._retries.append(now)
            return True
        return False

    def stats(self) -> dict[str, int]:
        self._trim(self._clock())
        return {"requests": len(self._requests), "retries": len(self._retries)}


class CircuitBreaker:
    """CLOSED, OPEN and HALF_OPEN, as in design figure 5.4.

    Opens after `consecutive_failures` failures in a row, or when the failure rate
    over the window reaches `failure_rate` with at least `min_calls` calls. After the
    cool-down it lets one probe through at a time; `probes_needed` successes close it,
    one failure reopens it with the cool-down doubled up to `max_cooldown`.

    Only transient failures should be recorded as failures: a 409 means the simulator is
    healthy and said no, so it is a success here.
    """

    def __init__(
        self,
        name: str,
        *,
        consecutive_failures: int = 5,
        failure_rate: float = 0.5,
        min_calls: int = 10,
        window: float = 30.0,
        cooldown: float = 5.0,
        max_cooldown: float = 30.0,
        probes_needed: int = 2,
        clock: Clock = time.monotonic,
    ) -> None:
        self.name = name
        self._consecutive_threshold = consecutive_failures
        self._rate_threshold = failure_rate
        self._min_calls = min_calls
        self._window = window
        self._base_cooldown = cooldown
        self._max_cooldown = max_cooldown
        self._probes_needed = probes_needed
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._cooldown = cooldown
        self._opened_at = 0.0
        self._consecutive = 0
        self._calls: deque[tuple[float, bool]] = deque()
        self._probe_in_flight = False
        self._probe_successes = 0
        self.opened_total = 0

    @property
    def state(self) -> BreakerState:
        return self._state

    def _open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._probe_in_flight = False
        self.opened_total += 1

    def allow(self) -> bool:
        """May a request be sent now? In HALF_OPEN only one probe is in flight at a time."""
        if self._state is BreakerState.CLOSED:
            return True
        if self._state is BreakerState.OPEN:
            if self._clock() - self._opened_at >= self._cooldown:
                self._state = BreakerState.HALF_OPEN
                self._probe_successes = 0
                self._probe_in_flight = True
                return True
            return False
        # HALF_OPEN
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        if self._state is BreakerState.HALF_OPEN:
            self._probe_in_flight = False
            self._probe_successes += 1
            if self._probe_successes >= self._probes_needed:
                self._state = BreakerState.CLOSED
                self._cooldown = self._base_cooldown
                self._consecutive = 0
                self._calls.clear()
        elif self._state is BreakerState.CLOSED:
            self._consecutive = 0
            self._remember(True)
        # a late success arriving while OPEN says nothing about the present: ignore

    def record_failure(self) -> None:
        if self._state is BreakerState.HALF_OPEN:
            self._cooldown = min(self._cooldown * 2, self._max_cooldown)
            self._open()
            return
        if self._state is BreakerState.OPEN:
            return
        self._consecutive += 1
        self._remember(False)
        failures = sum(1 for _, ok in self._calls if not ok)
        rate_tripped = len(self._calls) >= self._min_calls and failures / len(self._calls) >= self._rate_threshold
        if self._consecutive >= self._consecutive_threshold or rate_tripped:
            self._open()

    def _remember(self, ok: bool) -> None:
        now = self._clock()
        cutoff = now - self._window
        while self._calls and self._calls[0][0] < cutoff:
            self._calls.popleft()
        self._calls.append((now, ok))

    def info(self) -> dict[str, Any]:
        return {"state": self._state.value, "opened_total": self.opened_total, "cooldown_seconds": self._cooldown}
