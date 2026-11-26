"""Demand anomaly detection (design 6.5): pure logic, no I/O.

Per station, all fuels summed, per tick: z = (observed - forecast) / sigma, where the forecast and its
spread are the forecaster's one-step-ahead figures, taken before that tick was learned. Two residuals are
kept for each tick:

* against the full forecast, which includes the announced demand spikes: a departure here is one the
  known events do not account for;
* against the normal forecast, with the spikes left out: a departure only here is one they do account for.

A station is flagged when at least 2 of its last 3 consecutive ticks depart the same way. A tick departs
when |z| > 3, and still does after centring and widening by the median and MAD of the station's earlier z,
and the gap is more than MIN_DEVIATION of the forecast. The robust and size tests can only suppress a flag.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from .forecast import spike_multiplier
from .models import Event, EventType, Station, StationStatus
from .snapshot import WorldSnapshot

Z_LIMIT = 3.0
FLAG_WINDOW, FLAG_NEEDED = 3, 2
MIN_HISTORY = 16  # earlier ticks a station needs before one of its ticks can be flagged (four simulated hours)
BASELINE_TICKS = 48  # earlier z values behind the median and MAD
MAD_TO_SD = 1.4826
# The simulator's noise is at most 12% either way (design 6.1), so a smaller gap is not worth an operator's time.
MIN_DEVIATION = 0.15
SIGMA_FLOOR_REL, SIGMA_FLOOR_LITERS = 0.03, 1.0  # a tiny sigma must not turn a tiny gap into a huge z
MAX_LAG_TICKS = 4  # observations older than this say nothing about the present


@dataclass(frozen=True)
class Anomaly:
    station_id: str
    station_name: str
    kind: str  # explained or unexplained
    direction: str  # above or below
    deviation: float  # observed / expected - 1 over the flagged ticks; "expected" is the normal forecast when explained
    z: float  # of the newest flagged tick
    first_tick: int  # first flagged tick of this episode
    last_tick: int  # newest flagged tick
    summary: str


@dataclass(frozen=True)
class _Side:
    """One tick measured against one of the two forecasts."""

    forecast: float
    z: float
    sign: int  # +1 or -1 when the tick departs, 0 when it does not


@dataclass(frozen=True)
class _Tick:
    tick: int
    observed: float
    spread: float  # sigma relative to the full forecast
    full: _Side
    normal: _Side


@dataclass
class _StationState:
    z: deque[float] = field(default_factory=lambda: deque(maxlen=BASELINE_TICKS))
    recent: deque[_Tick] = field(default_factory=lambda: deque(maxlen=FLAG_WINDOW))
    last_tick: int = -1
    opened: int | None = None


def _in_outage(station_id: str, tick: int, events: Sequence[Event]) -> bool:
    return any(
        e.type is EventType.STATION_OUTAGE and e.start_tick <= tick <= e.end_tick and (not e.parameters.get("station_ids") or station_id in e.parameters["station_ids"])
        for e in events
    )


def _side(observed: float, forecast: float, sigma: float, history: Sequence[float]) -> _Side:
    sigma = max(sigma, SIGMA_FLOOR_REL * forecast, SIGMA_FLOOR_LITERS)
    z = (observed - forecast) / sigma
    sign = 0
    if len(history) >= MIN_HISTORY and abs(z) > Z_LIMIT and abs(observed / forecast - 1.0) > MIN_DEVIATION:
        median = statistics.median(history)
        mad = statistics.median(abs(v - median) for v in history)
        robust = (z - median) / max(1.0, MAD_TO_SD * mad)
        if abs(robust) > Z_LIMIT and (robust > 0) == (z > 0):
            sign = 1 if z > 0 else -1
    return _Side(forecast, z, sign)


def _flagged(ticks: Sequence[_Tick], which: str) -> tuple[int, list[_Tick]]:
    """The direction and the departing ticks when enough of the window departs the same way, else (0, [])."""
    for sign in (1, -1):
        hits = [t for t in ticks if getattr(t, which).sign == sign]
        if len(hits) >= FLAG_NEEDED:
            return sign, hits
    return 0, []


def _deviation(hits: Sequence[_Tick], which: str) -> float:
    return sum(t.observed for t in hits) / sum(getattr(t, which).forecast for t in hits) - 1.0


def _percent(deviation: float) -> str:
    return f"{abs(deviation):.0%} {'above' if deviation > 0 else 'below'}"


def _spikes(station: Station, ticks: Sequence[int], events: Sequence[Event]) -> list[Event]:
    return [e for e in events if e.type is EventType.DEMAND_SPIKE and any(spike_multiplier(station, t, [e]) != 1.0 for t in ticks)]


def _describe(spikes: Sequence[Event]) -> str:
    return " and ".join(f"demand spike x{float(e.parameters.get('multiplier', 1.0)):g}, started tick {e.start_tick}" for e in sorted(spikes, key=lambda e: (e.start_tick, e.id)))


class AnomalyDetector:
    def __init__(self) -> None:
        self._stations: dict[str, _StationState] = {}

    def reset(self) -> None:
        """Drop everything: a new world shares no history with the old one."""
        self._stations.clear()

    def observe(self, station: Station, tick: int, parts: Sequence[tuple[float, float, float, float]], events: Sequence[Event]) -> None:
        """One station's tick. Each part is one fuel: (observed, normal forecast, spike multiplier, relative sigma).

        Ticks must arrive in order; a repeated or older tick is ignored. Outage ticks are not demand to learn from.
        """
        state = self._stations.setdefault(station.id, _StationState())
        if tick <= state.last_tick:
            return
        state.last_tick = tick
        if _in_outage(station.id, tick, events):
            state.recent.clear()
            return
        observed = normal = full = var_normal = var_full = 0.0
        for y, base, m, rel in parts:
            observed += y
            normal += base
            full += base * m
            var_normal += (rel * base) ** 2
            var_full += (rel * base * m) ** 2
        if not (normal > 0 and full > 0 and math.isfinite(observed + normal + full + var_normal + var_full)):
            return
        history = list(state.z)
        full_side = _side(observed, full, math.sqrt(var_full), history)
        state.recent.append(_Tick(tick, observed, math.sqrt(var_full) / full, full_side, _side(observed, normal, math.sqrt(var_normal), history)))
        state.z.append(full_side.z)

    def evaluate(self, snapshot: WorldSnapshot) -> tuple[Anomaly, ...]:
        """The anomalies open now, unexplained first."""
        out: list[Anomaly] = []
        for station in snapshot.stations:
            state = self._stations.get(station.id)
            if state is None:
                continue
            found = None if station.status is StationStatus.OUTAGE else self._assess(station, state, snapshot)
            if found is None:
                state.opened = None
            else:
                out.append(found)
        return tuple(sorted(out, key=lambda a: (a.kind != "unexplained", -abs(a.deviation), a.station_id)))

    def _assess(self, station: Station, state: _StationState, snapshot: WorldSnapshot) -> Anomaly | None:
        if state.last_tick < snapshot.tick - MAX_LAG_TICKS:
            return None
        window = [t for t in state.recent if t.tick > state.last_tick - FLAG_WINDOW]  # a gap leaves fewer ticks, never older ones
        sign, hits = _flagged(window, "full")
        if sign:  # the announced spikes do not account for it; the station's own multiplier still may
            deviation = _deviation(hits, "full")
            spikes = _spikes(station, [t.tick for t in hits], snapshot.events)
            kind = "unexplained"
            summary = f"Demand {_percent(deviation)} forecast, " + (f"more than the {_describe(spikes)} accounts for" if spikes else "no known cause")
            # The part of the station's multiplier that no event in the forecast already covers.
            extra = station.demand_multiplier / (spike_multiplier(station, snapshot.tick, snapshot.events) or 1.0)
            own = station.demand_multiplier - 1.0
            if abs(own) > MIN_DEVIATION and abs(extra - 1.0) > MIN_DEVIATION and (own > 0) == (extra > 1) == (sign > 0):
                tolerance = max(MIN_DEVIATION, Z_LIMIT * max(t.spread for t in hits))
                if sign * ((1.0 + deviation) / extra - 1.0) <= tolerance:  # no further out than the multiplier explains
                    kind = "explained"
                    summary = f"Demand {_percent(deviation)} forecast: the station's demand multiplier is {station.demand_multiplier:g}"
        else:
            sign, hits = _flagged(window, "normal")
            if not sign:
                return None
            spikes = _spikes(station, [t.tick for t in hits], snapshot.events)
            if not spikes:
                return None  # with no spike the two forecasts are the same, so there is nothing to report
            deviation = _deviation(hits, "normal")
            kind = "explained"
            summary = f"Demand {_percent(deviation)} normal: {_describe(spikes)}"
        if state.opened is None:
            state.opened = hits[0].tick
        newest = hits[-1]
        z = newest.full.z if newest.full.sign else newest.normal.z
        return Anomaly(station.id, station.name, kind, "above" if sign > 0 else "below", round(deviation, 4), round(z, 2), state.opened, newest.tick, summary)
