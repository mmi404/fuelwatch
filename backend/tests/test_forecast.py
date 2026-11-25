import math
import random

import pytest

from fuelwatch.forecast import DAILY_LITERS, DemandForecaster, Z90, hour_of_day, hour_shape, spike_multiplier
from fuelwatch.models import Event, Region, Station

REGION = Region(id="region-dhaka", name="Dhaka", demand_factor=1.0)


def station(profile="urban_high", sid="station-mirpur", region="region-dhaka") -> Station:
    caps = {"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000}
    return Station(id=sid, name=sid, region_id=region, status="OPEN", demand_profile=profile, demand_multiplier=1.0, capacity=caps, inventory=caps)


def spike(start, end, multiplier=2.0, **params) -> Event:
    return Event(id=start, type="demand_spike", start_tick=start, end_tick=end, status="RESOLVED", parameters={"multiplier": multiplier, **params})


def generate(st, fuel, ticks, events=(), noise=0.10, volume=1.0, seed=1, tick_minutes=15):
    """Demand exactly as the simulator makes it (design 6.1), with a volume knob for a differently configured world."""
    rng = random.Random(seed)
    rows = []
    for t in range(ticks):
        base = DAILY_LITERS[st.demand_profile][fuel] / 96.0 * hour_shape(st.demand_profile, hour_of_day(t, tick_minutes)) * REGION.demand_factor
        rows.append((st.id, fuel, t, base * volume * spike_multiplier(st, t, events) * (1 + rng.uniform(-noise, noise))))
    return rows


def trained(rows, st, events=()):
    f = DemandForecaster()
    for t in sorted({r[2] for r in rows}):
        f.train([r for r in rows if r[2] == t], [st], [REGION], list(events), 15)
    return f


# -- the structure ------------------------------------------------------------------------


def test_hour_of_day_uses_tick_minutes():
    assert [hour_of_day(t, 15) for t in (0, 3, 4, 95, 96)] == [0, 0, 1, 23, 0]
    assert hour_of_day(10, 8) == 1  # 80 minutes


@pytest.mark.parametrize("start,end,expected", [(4, 8, {3: 1.0, 4: 2.0, 8: 2.0, 9: 1.0})])
def test_spike_covers_start_to_end_inclusive(start, end, expected):
    st = station()
    for t, m in expected.items():
        assert spike_multiplier(st, t, [spike(start, end)]) == m  # [live]: end_tick is inclusive


def test_spike_scope_station_region_or_everyone_and_stacking():
    mirpur, cox = station(), station("regional", "station-coxsbazar", "region-chattogram")
    only_mirpur = spike(0, 5, 3.0, station_ids=["station-mirpur"])
    dhaka = spike(0, 5, 2.0, region_ids=["region-dhaka"])
    everyone = spike(0, 5, 1.5)
    assert spike_multiplier(mirpur, 1, [only_mirpur]) == 3.0 and spike_multiplier(cox, 1, [only_mirpur]) == 1.0
    assert spike_multiplier(mirpur, 1, [dhaka]) == 2.0 and spike_multiplier(cox, 1, [dhaka]) == 1.0
    assert spike_multiplier(cox, 1, [everyone]) == 1.5
    assert spike_multiplier(mirpur, 1, [only_mirpur, dhaka, everyone]) == pytest.approx(9.0)  # spikes stack


def test_cold_start_forecast_is_the_prior_before_any_data():
    f = DemandForecaster()
    st = station()
    f.prime(st, "DIESEL", [REGION])
    expected = 8500 / 96 * hour_shape("urban_high", 8)
    assert f.tick_forecast(st, "DIESEL", 32, 15, []) == pytest.approx(expected)  # tick 32 = 08:00
    assert f.observations("station-mirpur", "DIESEL") == 0


def test_unknown_profile_has_no_forecast_until_data_arrives():
    f = DemandForecaster()
    st = station("mystery")
    f.prime(st, "DIESEL", [REGION])
    assert f.tick_forecast(st, "DIESEL", 0, 15, []) is None and not f.ready(st.id, "DIESEL")
    f.train([(st.id, "DIESEL", t, 120.0) for t in range(6)], [st], [REGION], [], 15)
    assert f.tick_forecast(st, "DIESEL", 7, 15, []) == pytest.approx(120.0)


# -- accuracy -------------------------------------------------------------------------------


def test_accuracy_reaches_the_noise_floor_and_the_interval_is_calibrated():
    st = station()
    f = trained(generate(st, "PETROL", 96 * 3), st)
    acc = f.accuracy()["series"]["station-mirpur/PETROL"]
    # the noise floor for +-10% uniform is 5% mean absolute error; the target is within 2 points of it
    assert acc["mape"] < 0.07
    cov = f.accuracy()["coverage_80"]
    assert 0.72 <= cov <= 0.92


def test_a_world_with_different_volumes_is_learned_quickly():
    st = station()
    rows = generate(st, "DIESEL", 96 * 2, volume=1.6)  # the prior says 1.0, the world says 1.6
    f = trained(rows, st)
    later = [r for r in generate(st, "DIESEL", 96 * 3, volume=1.6, seed=2) if r[2] >= 96 * 2]
    errs = []
    for _, _, t, actual in later:
        fc = f.tick_forecast(st, "DIESEL", t, 15, [])
        errs.append(abs(actual - fc) / actual)
    assert sum(errs) / len(errs) < 0.08


def test_it_beats_seasonal_naive_and_last_value_after_a_day():
    st = station("industrial", "station-tongi")
    rows = generate(st, "DIESEL", 96 * 3, noise=0.08, seed=5)
    f = DemandForecaster()
    model_err = naive_err = last_err = 0.0
    n = 0
    actual = {r[2]: r[3] for r in rows}
    for t in range(96 * 3):
        if t >= 96 * 2:
            fc = f.tick_forecast(st, "DIESEL", t, 15, [])
            model_err += abs(actual[t] - fc) / actual[t]
            naive_err += abs(actual[t] - actual[t - 96]) / actual[t]
            last_err += abs(actual[t] - actual[t - 1]) / actual[t]
            n += 1
        f.train([r for r in rows if r[2] == t], [st], [REGION], [], 15)
    assert model_err / n < naive_err / n and model_err / n < last_err / n


# -- spikes ---------------------------------------------------------------------------------


def test_spikes_are_divided_out_of_training_and_applied_to_the_future():
    st = station()
    events = [spike(30, 36, 2.0)]
    f = trained(generate(st, "DIESEL", 96 * 2, events), st, events)
    quiet = f.tick_forecast(st, "DIESEL", 96 * 2 + 31, 15, [])  # hour 7.75 on day 3, no spike
    with_future_spike = f.tick_forecast(st, "DIESEL", 96 * 2 + 31, 15, [spike(96 * 2 + 30, 96 * 2 + 36, 2.0)])
    assert with_future_spike == pytest.approx(2 * quiet)
    # a model that ignored the spike would have its hour-7 level inflated; this one stays on the true level
    true_level = 8500 / 96 * 1.45
    assert quiet == pytest.approx(true_level, rel=0.06)


# -- uncertainty -----------------------------------------------------------------------------


def test_cumulative_follows_the_variance_formula():
    st = station()
    f = trained(generate(st, "DIESEL", 96), st)
    cum = f.cumulative(st, "DIESEL", 96, 4, 15, [])
    series = f._series[(st.id, "DIESEL")]
    sigma, eps = series.sigma(), series.eps_level()
    fcs = [f.tick_forecast(st, "DIESEL", 96 + i, 15, []) for i in range(4)]
    assert cum[3].mean == pytest.approx(sum(fcs))
    expected_var = sum((sigma * x) ** 2 for x in fcs) + (eps * sum(fcs)) ** 2
    assert cum[3].sd == pytest.approx(math.sqrt(expected_var))
    assert cum[3].p90 == pytest.approx(cum[3].mean + Z90 * cum[3].sd)
    assert all(b.sd > a.sd for a, b in zip(cum, cum[1:]))  # wider the further ahead


def test_reset_forgets_everything():
    st = station()
    f = trained(generate(st, "DIESEL", 50), st)
    assert f.observations(st.id, "DIESEL") == 50
    f.reset()
    assert f.observations(st.id, "DIESEL") == 0 and f.trained_through == -1


def test_already_learned_ticks_are_not_counted_twice():
    st = station()
    rows = generate(st, "DIESEL", 10)
    f = DemandForecaster()
    f.train(rows, [st], [REGION], [], 15)
    f.train(rows, [st], [REGION], [], 15)
    assert f.observations(st.id, "DIESEL") == 10
