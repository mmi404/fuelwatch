"""Demand forecaster (design part 6.2).

The simulator's demand is `daily/96 x hour_factor(profile, hour) x region_factor x
spike multiplier x (1 + noise)` (`[code]`, checked on 1,572 rows). The forecaster
is built on that structure:

    forecast(s, f, t) = level(s, f, hour(t)) x M(s, t) x A(s, f)

* `level` is learned per hour-of-day bin from history with the spike multiplier divided
  out. A bin's estimate is a posterior mean that starts from a prior (the profile's two-level
  shape times a scale) and lets data take over after a few observations.
* `M(s, t)` is the spike multiplier, rebuilt from the events list, so past spikes are divided
  out of the training data and scheduled spikes are applied to future ticks.
* `A` is a slow adaptive ratio of realised to forecast demand, clipped, which catches change
  the model does not know about.

Training is prequential: each tick is forecast before it is learned, so the residuals that
set the uncertainty (and the accuracy figures) are honest one-step-ahead errors.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .models import Event, EventType, Region, Station

# Profile constants read from the simulator's baseline scenario and engine (`[code]`, design 6.1).
# They are a prior only: the scale is re-learned from data after a few ticks.
DAILY_LITERS: dict[str, dict[str, float]] = {
    "urban_high": {"DIESEL": 8500, "PETROL": 10500, "OCTANE": 5600},
    "industrial": {"DIESEL": 14000, "PETROL": 4500, "OCTANE": 2200},
    "highway": {"DIESEL": 10500, "PETROL": 11000, "OCTANE": 6200},
    "regional": {"DIESEL": 7200, "PETROL": 7600, "OCTANE": 3600},
}
NOISE_PRIOR: dict[str, float] = {"urban_high": 0.10, "industrial": 0.08, "highway": 0.12, "regional": 0.10}
DEFAULT_NOISE = 0.10

N0 = 8.0  # pseudo-observations behind the prior: data dominates after a few ticks
SIGMA_WEIGHT = 20.0  # pseudo-observations behind the prior residual spread
A_ALPHA = 0.05
A_MIN, A_MAX = 0.5, 3.0
Z90 = 1.2816  # standard normal 90th percentile


def hour_of_day(tick: int, tick_minutes: int) -> int:
    return (tick * tick_minutes // 60) % 24


def hour_shape(profile: str, hour: int) -> float:
    """The profile's step function (two levels). Unknown profiles are flat and learned from data."""
    if profile == "industrial":
        return 1.55 if 6 <= hour < 18 else 0.45
    if profile == "highway":
        return 1.35 if 6 <= hour < 10 or 16 <= hour < 21 else 0.75
    if profile == "urban_high":
        return 1.45 if 7 <= hour < 10 or 16 <= hour < 21 else 0.70
    if profile == "regional":
        return 1.25 if 7 <= hour < 21 else 0.65
    return 1.0


def spike_multiplier(station: Station, tick: int, events: Iterable[Event]) -> float:
    """Product of the demand spikes covering `tick` that apply to the station.

    A spike covers start_tick <= t <= end_tick (the end tick is inclusive, `[live]`) and applies to
    the stations named, to the stations of the regions named, or to every station if it names neither.
    """
    m = 1.0
    for e in events:
        if e.type is not EventType.DEMAND_SPIKE or not (e.start_tick <= tick <= e.end_tick):
            continue
        stations = e.parameters.get("station_ids")
        regions = e.parameters.get("region_ids")
        if stations or regions:
            if not ((stations and station.id in stations) or (regions and station.region_id in regions)):
                continue
        try:
            m *= float(e.parameters.get("multiplier", 1.0))
        except (TypeError, ValueError):
            continue
    return m


@dataclass
class _Series:
    """Learned state for one station and fuel."""

    profile: str
    prior_scale: float | None  # litres per tick at shape 1.0, from the baseline constants
    prior_noise: float
    scale_n: int = 0  # observations behind the global scale
    scale_sum: float = 0.0  # sum of y / shape
    bin_n: list[int] = field(default_factory=lambda: [0] * 24)
    bin_sum: list[float] = field(default_factory=lambda: [0.0] * 24)
    resid_n: int = 0
    resid_sq: float = 0.0
    a: float = 1.0
    abs_pct_n: int = 0
    abs_pct_sum: float = 0.0
    covered: int = 0  # one-step-ahead 80% interval hits

    def scale(self) -> float | None:
        """Posterior mean of the global scale: prior weight N0, then data."""
        if self.prior_scale is None:
            return self.scale_sum / self.scale_n if self.scale_n else None
        return (N0 * self.prior_scale + self.scale_sum) / (N0 + self.scale_n)

    def level(self, hour: int) -> float | None:
        c = self.scale()
        if c is None:
            return None
        prior = c * hour_shape(self.profile, hour)
        return (N0 * prior + self.bin_sum[hour]) / (N0 + self.bin_n[hour])

    def sigma(self) -> float:
        """Relative spread of the one-step residual: a prior of the profile's noise, then data."""
        prior_var = (self.prior_noise**2) / 3.0  # a uniform +-n has variance n^2 / 3
        return math.sqrt((SIGMA_WEIGHT * prior_var + self.resid_sq) / (SIGMA_WEIGHT + self.resid_n))

    def eps_level(self) -> float:
        """Relative uncertainty of the learned level: shrinks as 1 / sqrt(observations)."""
        return self.sigma() / math.sqrt(max(1, self.scale_n))


@dataclass(frozen=True)
class Cumulative:
    mean: float
    sd: float

    @property
    def p90(self) -> float:
        return self.mean + Z90 * self.sd


class DemandForecaster:
    def __init__(self) -> None:
        self._series: dict[tuple[str, str], _Series] = {}
        self._last_tick: dict[tuple[str, str], int] = {}  # newest tick learned per station and fuel
        self.trained_through = -1  # newest tick learned anywhere

    def reset(self) -> None:
        """Drop everything learned: called when the world is reset."""
        self._series.clear()
        self._last_tick.clear()
        self.trained_through = -1

    # -- training ---------------------------------------------------------------------

    def _get(self, station: Station, fuel: str, regions: dict[str, Region]) -> _Series:
        key = (station.id, fuel)
        series = self._series.get(key)
        if series is None:
            daily = DAILY_LITERS.get(station.demand_profile, {}).get(fuel)
            region = regions.get(station.region_id)
            prior_scale = None
            if daily is not None and region is not None:
                prior_scale = daily / 96.0 * region.demand_factor
            series = _Series(station.demand_profile, prior_scale, NOISE_PRIOR.get(station.demand_profile, DEFAULT_NOISE))
            self._series[key] = series
        return series

    def train(self, rows: Sequence[tuple[str, str, int, float]], stations: Sequence[Station], regions: Sequence[Region], events: Sequence[Event], tick_minutes: int) -> int:
        """Learn rows of (station_id, fuel, tick, demand) with tick above `trained_through`.

        Rows must be complete ticks. Returns the number of rows learned.
        """
        by_station = {s.id: s for s in stations}
        by_region = {r.id: r for r in regions}
        learned = 0
        for station_id, fuel, tick, demand in sorted(rows, key=lambda r: r[2]):
            station = by_station.get(station_id)
            if station is None or tick <= self.trained_through_for(station_id, fuel):
                continue
            series = self._get(station, fuel, by_region)
            hour = hour_of_day(tick, tick_minutes)
            m = spike_multiplier(station, tick, events)
            y = demand / m if m > 0 else demand

            # One-step-ahead check before learning this tick.
            predicted = series.level(hour)
            if predicted is not None and demand > 0:
                base = predicted * m
                forecast = base * series.a
                r = demand / forecast - 1.0
                series.resid_n += 1
                series.resid_sq += r * r
                series.abs_pct_n += 1
                series.abs_pct_sum += abs(demand - forecast) / demand
                s_rel = math.sqrt(series.sigma() ** 2 + series.eps_level() ** 2)
                if abs(r) <= Z90 * s_rel:
                    series.covered += 1
                series.a = min(A_MAX, max(A_MIN, (1 - A_ALPHA) * series.a + A_ALPHA * (demand / base)))

            shape = hour_shape(series.profile, hour)
            series.scale_n += 1
            series.scale_sum += y / shape
            series.bin_n[hour] += 1
            series.bin_sum[hour] += y
            self._last_tick[(station_id, fuel)] = tick
            learned += 1
        if rows:
            self.trained_through = max(self.trained_through, max(r[2] for r in rows))
        return learned

    def trained_through_for(self, station_id: str, fuel: str) -> int:
        return self._last_tick.get((station_id, fuel), -1)

    # -- forecasting --------------------------------------------------------------------

    def ready(self, station_id: str, fuel: str) -> bool:
        s = self._series.get((station_id, fuel))
        return s is not None and s.level(0) is not None

    def observations(self, station_id: str, fuel: str) -> int:
        s = self._series.get((station_id, fuel))
        return s.scale_n if s else 0

    def prime(self, station: Station, fuel: str, regions: Sequence[Region]) -> None:
        """Make sure a series exists so the prior can be used before any data."""
        self._get(station, fuel, {r.id: r for r in regions})

    def tick_forecast(self, station: Station, fuel: str, tick: int, tick_minutes: int, events: Sequence[Event]) -> float | None:
        s = self._series.get((station.id, fuel))
        if s is None:
            return None
        level = s.level(hour_of_day(tick, tick_minutes))
        if level is None:
            return None
        return level * spike_multiplier(station, tick, events) * s.a

    def cumulative(self, station: Station, fuel: str, from_tick: int, n: int, tick_minutes: int, events: Sequence[Event]) -> list[Cumulative] | None:
        """Cumulative demand over the next 1..n ticks from `from_tick`, as mean and standard deviation.

        var(n) = sum over ticks of (sigma x forecast)^2  +  (eps_level x mean(n))^2:
        independent tick noise plus the fully correlated uncertainty of the learned level.
        """
        s = self._series.get((station.id, fuel))
        if s is None or s.level(0) is None:
            return None
        sigma, eps = s.sigma(), s.eps_level()
        mean = 0.0
        noise_var = 0.0
        out: list[Cumulative] = []
        for i in range(n):
            f = self.tick_forecast(station, fuel, from_tick + i, tick_minutes, events)
            assert f is not None
            mean += f
            noise_var += (sigma * f) ** 2
            out.append(Cumulative(mean, math.sqrt(noise_var + (eps * mean) ** 2)))
        return out

    # -- diagnostics ----------------------------------------------------------------------

    def accuracy(self) -> dict[str, object]:
        """Prequential accuracy, pooled and per station and fuel: mean absolute percentage error and 80% coverage."""
        total_n = sum(s.abs_pct_n for s in self._series.values())
        pooled_mape = sum(s.abs_pct_sum for s in self._series.values()) / total_n if total_n else None
        pooled_cov = sum(s.covered for s in self._series.values()) / total_n if total_n else None
        return {
            "trained_through_tick": self.trained_through,
            "forecasts_scored": total_n,
            "mape": pooled_mape,
            "coverage_80": pooled_cov,
            "series": {
                f"{sid}/{fuel}": {
                    "observations": s.scale_n,
                    "mape": (s.abs_pct_sum / s.abs_pct_n) if s.abs_pct_n else None,
                    "sigma": round(s.sigma(), 4),
                    "noise_floor_mape": round(s.prior_noise / 2, 4),
                    "adaptive_multiplier": round(s.a, 4),
                }
                for (sid, fuel), s in sorted(self._series.items())
            },
        }
