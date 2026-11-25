# 6. Algorithms

This part is the specification for the intelligence layer, written so that each piece can be tested on its own and compared in the replay benchmark.

Notation. `T` is the snapshot tick (the next tick to be processed). `s` is a station, `f` a fuel, `k` a depot, `r` a route with `transit_ticks` `tau_r` and `max_shipment` `m_r`. `I` is inventory, `cap` capacity, `d` demand per tick. Hours are `ticks x tick_minutes / 60`.

## 6.1 What the world does (the model the forecaster is built on)

`[code]` `[live]` Demand per tick for station `s` and fuel `f` is:

```
demand(s, f, t) = daily(profile_s, f) / 96
                  x hour_factor(profile_s, hour(t))
                  x region_factor(s)
                  x demand_multiplier(s, t)
                  x (1 + U(-noise_s, +noise_s))
```

`hour(t) = (t x tick_minutes / 60) mod 24`. The hour factor is a step function with two levels per profile (table in the findings, section 3 item 8). The noise is independent between ticks and between fuels. `demand_multiplier` is 1.0 unless a `demand_spike` is active; spikes multiply it and are divided out when they end.

This was checked on 1,572 observed rows: none fell outside the band the formula predicts. Two properties follow and drive everything below:

1. **The structure is learnable from a few hours of data.** Four profiles, two levels each, one shared shape across the three fuels of a station.
2. **The irreducible error is the noise itself.** For uniform noise of plus or minus `n`, the mean absolute error of a perfect forecast is `n / 2` (5% for `n = 10%`). No model can do better, so forecast accuracy is reported against that floor, not against zero.

## 6.2 Demand forecaster

### Model

```
forecast(s, f, t) = level(s, f, k) x M(s, t) x A(s, t)        with k = hour of day of t
```

- **`level(s, f, k)`**: expected demand per tick in hour-of-day bin `k`, with the spike multiplier divided out. Learned from history.
- **`M(s, t)`**: the demand multiplier at tick `t`. A spike covers `start_tick <= t <= end_tick` (the end tick is inclusive, so a duration of 4 starting at tick 5 covers ticks 5 to 9) and applies to the stations named in `station_ids`, to the stations of the regions in `region_ids`, or to every station if it names neither; spikes stack (`[live]`, injected and read back). A `station_outage` covers its ticks the same way, serves nothing, and leaves the recorded demand unchanged. The current value is read from the snapshot (`demand_multiplier`); future values come from `/v1/events` (spikes with `status` `SCHEDULED` or `ACTIVE` and their `start_tick` and `end_tick`). Historical values, needed to divide spikes out of past demand, are rebuilt from the same event list, which keeps resolved events (`GET /v1/events` applies no filter, `[code]`).
- **`A(s, t)`**: an adaptive safety multiplier, an exponentially weighted ratio of realised to forecast demand, clipped to `[0.5, 3]`. Because `M` already follows spikes, `A` stays near 1 here; it exists to catch changes the model does not know (a differently configured simulator, an event type added later).

### Learning with a cold start

At tick 0 there is no history, and the first stockout is 65 ticks away, so the forecaster must be useful immediately.

| History available | Estimate for a bin `k` |
|---|---|
| None | Prior only: the profile's daily litres and two-level shape, read from the simulator's baseline scenario and engine (`[code]`, findings section 3 item 8), times the region factor. A profile the table does not know has no forecast until data arrives, and its assessment is `UNKNOWN` |
| Some bins seen | Two posterior means with `n0 = 8` pseudo-observations each, `(n0 x prior + sum of observations) / (n0 + n)`: first the station and fuel's overall scale from every tick seen (this is what replaces a wrong prior within a few ticks), then each hour-of-day bin, whose prior is that scale times the profile's shape |
| A full day (96 ticks) | Data dominates; the prior has a weight of under 10% |

The scale is learned per station and fuel from one observation per tick, which is enough because the shape is known and the noise is small; pooling the three fuels of a station was considered and not needed (ADR-05).

Confidence is lowered while history is under one day (section 6.7), and the interface says so. The recommendation still works in that window, because the allocator targets a **cover** (hours of stock after arrival), which tolerates a rough forecast.

### Uncertainty

Per tick, the residual `r = demand / forecast - 1` is tracked per station and fuel, giving an empirical spread `sigma_r`. The cumulative demand over the next `n` ticks has:

```
mean(n) = sum of forecast(t)                              for t in T .. T+n-1
var(n)  = sum of (sigma_r x forecast(t))^2  +  (eps_level x mean(n))^2
```

The first term is independent tick noise; the second is the uncertainty in the learned level (`eps_level` shrinks as `1 / sqrt(observations)`) and is fully correlated across ticks. The P90 cumulative demand is `mean(n) + 1.28 x sqrt(var(n))`. Using the P90 of each tick and summing would overstate the total, so the quantile is taken on the cumulative.

### Evaluation

Rolling-origin evaluation on the replay: forecast 1, 4 and 8 ticks ahead at every tick and compare with what happened.

| Metric | Definition | Target |
|---|---|---|
| Mean absolute percentage error | mean of `abs(actual - forecast) / actual` per tick | Within 2 points of the noise floor (5% for `n = 10%`) once a day of history exists |
| 80% interval coverage | share of realised one-step demand inside the P10 to P90 band | Between 72% and 85%. The noise is uniform, which has lighter tails than the normal the band assumes: `1.28 sigma` covers 74% of a uniform, so about 74% is what a perfect model scores |
| Baseline | Seasonal-naive (same hour yesterday) and last-value | The model must beat both after one day |

Measured: on synthetic demand generated exactly like the simulator's (`[local]`, `tests/test_forecast.py`) the error is within 0.3 points of the noise floor after three days (5.3% against 5.0% for `urban_high`) and the model beats both baselines. On the live simulator over 98 ticks with nothing allocated (`[live]`) the pooled error was 5.2% and the band covered 78%; the per-series errors sit within 0.5 points of their floors. Against the first stockouts that actually happened (Tongi diesel at tick 65, Karnaphuli diesel 74, octane 76, petrol 79), the estimator's P50 and P90 were within 0 to 3 ticks from tick 0 onward, early at P90 as intended, and at tick 6 the cold start was already as good as at tick 48. The simulator's baseline constants are the prior, so the tick-0 figure shows the prior is right for this scenario, not that learning is unnecessary; a test with a different volume checks that learning takes over.

## 6.3 Stockout estimator

For each `(s, f)` project the inventory forward tick by tick, using the order the simulator uses within a tick: arrivals first, then demand.

```
for t = T, T+1, ...:
    incoming(t)  = sum of qty of allocations to s of fuel f with arrival tick t
    received(t)  = min(cap - I, incoming(t))          # the excess is lost
    I            = I + received(t)
    served(t)    = min(I, demand_q(t))                # demand_q is the P50 or P90 path; 0 if the station is in outage
    I            = I - served(t)
    if demand_q(t) - served(t) > 0 and first time:  stockout_tick = t
```

Arrival ticks: an `IN_TRANSIT` allocation arrives at its `expected_arrival_tick`; a `PENDING` one will depart in the next processing and arrives at `T + tau_r`.

Outputs per `(s, f)`:

| Output | Definition |
|---|---|
| `hours_p50`, `hours_p90` | `(stockout_tick - T) x tick_minutes / 60` using the mean path and the P90 cumulative path (capped at the horizon, 96 ticks, shown as "more than 24 h") |
| `risk` | Probability that cumulative demand over the next `W` ticks exceeds stock plus inbound within `W`: `1 - Phi((A - mean) / sqrt(var))`, with `W = 24` ticks initially |
| `level` | From `hours_p90`, see below |

| Level | Condition (initial values, tuned on the benchmark) |
|---|---|
| `CRITICAL` | `hours_p90` below the lead time of the best route plus 4 ticks, or below 4 h |
| `WARNING` | `hours_p90` below 12 h |
| `WATCH` | `hours_p90` below 24 h |
| `OK` | otherwise |

The alert inbox is ordered by `hours_p90`, so the most urgent is first and "who runs out next" is always answered at the top.

## 6.4 Allocation

### The rules the simulator enforces

A proposal must satisfy these, in the order the simulator checks them. They are inputs to both allocators.

| # | Rule | Check we apply |
|---|---|---|
| 1 | Route links the depot and station | Only routes from the snapshot |
| 2 | Depot open | `status` is `OPEN` or `CONSTRAINED` |
| 3 | Station open | `status` is `OPEN` (not `OUTAGE`) |
| 4 | Route available **at departure** | No `route_disruption` event, `SCHEDULED` or `ACTIVE`, covers tick `T + t` for the shipment's departure tick |
| 5 | Quantity not above the route maximum | `quantity <= m_r` per allocation |
| 6 | Depot stock enough | `quantity <= J(k, f)` |
| 7 | Depot dispatch capacity | Sum of the shipments created in the same tick from `k` is at most `dispatch_capacity_per_tick` |
| 8 | Destination headroom | `quantity <= cap - I` at POST time (the simulator ignores fuel on the road; we do not, see below) |

Rule 4 differs from the simulator's own check on purpose. The simulator checks the route's current status at POST, but fails the allocation at departure if the route has become disrupted in between. A `route_disruption` that starts exactly at tick `T` is still `SCHEDULED` when we POST, so the POST is accepted and the allocation then fails with no refund. The allocator therefore blocks a route for every tick covered by a disruption event, scheduled or active.

Rule 8 is stricter than the simulator's on purpose. The simulator compares the quantity with the station's *current* inventory only, so two shipments in flight can together overshoot, and the excess is lost on arrival. The allocator computes headroom at the projected arrival, net of everything already on the road.

### Policy 1: cover-based greedy (the fallback and a benchmark policy)

Simple, explainable, and robust to a rough forecast.

```
1. For every (s, f) compute the projection (6.3) and its P90 stockout tick.
2. Sort by hours_p90 ascending (most urgent first).
3. For each (s, f) with level != OK:
     arrival = T + tau of the best usable route
     target  = P75 cumulative demand over (arrival - T) + cover ticks   # cover = 24 ticks initially
     need    = target - (projected inventory at arrival, with inbound)
     need    = min(need, cap - projected inventory at arrival)          # no loss
     if need < min_shipment: skip
4. Choose among usable routes into s (rules 1-4): shortest transit first,
   ties to the depot with more stock of f.
5. quantity = min(need, m_r, depot stock - reserve, dispatch capacity left at the depot)
6. If quantity < need and another route exists, send the remainder there.
7. Record which constraint limited each shipment (binding constraint) for the explanation.
```

### Policy 2: rolling-horizon linear programme (the main policy)

Greedy decides one station at a time, so it can spend a depot's dispatch capacity on one station while another, more critical, one waits. The LP decides all stations together.

Decision epoch `T`, horizon `H` ticks (48 initially, 12 simulated hours), relative ticks `t = 0 .. H-1`.

| Symbol | Meaning |
|---|---|
| `x(r, f, t) >= 0` | Litres shipped on route `r`, fuel `f`, at relative tick `t` (the decision) |
| `u(s, f, t) >= 0` | Unmet demand |
| `I(s, f, t)` | Station inventory after serving tick `t` |
| `J(k, f, t)` | Depot inventory at the start of tick `t` |
| `g(k, f, t) >= 0` | Scheduled supply refused for lack of depot room |
| `z(s, f) >= 0` | Shortfall below the safety stock at the horizon end |

```
minimise   sum over s,f,t of  u(s,f,t)                        unmet litres (the score the simulator reports)
         + eps_ship   x sum of x(r,f,t)                        small cost, prevents pointless shipping
         + eps_lead   x sum of tau_r x x(r,f,t)                prefer fast routes among equals
         + c_waste    x sum of g(k,f,t)                        do not let a full depot refuse a delivery
         + c_safety   x sum of z(s,f)                          do not run every tank to zero at the horizon end

subject to
  station balance   I(s,f,t) = I(s,f,t-1) + arrivals_known(s,f,t) + sum over r into s of x(r,f,t - tau_r) - (d_q(s,f,t) - u(s,f,t))
  no loss           I(s,f,t-1) + arrivals_known(s,f,t) + sum x(r,f,t - tau_r)  <=  cap(s,f)
  demand bound      u(s,f,t) <= d_q(s,f,t),   and  u = d_q  while the station is in outage
  depot balance     J(k,f,t+1) = J(k,f,t) - sum over r from k of x(r,f,t) + supply(k,f,t) - g(k,f,t),   J <= cap_depot
  dispatch          sum over r from k, all f of x(r,f,t)  <=  dispatch_capacity(k)        for every t
  route limit       x(r,f,t)  <=  m_r
  blocked           x(r,f,t) = 0   if route r is covered by a disruption at T+t, or the destination is in outage
  safety stock      I(s,f,H-1) + z(s,f)  >=  safety(s,f)
  initial state     I(s,f,-1) = current inventory;  J(k,f,0) = current depot inventory
```

`d_q` is the demand path at the chosen risk quantile (P80 initially; P50, P80 and P90 are compared in the benchmark). `arrivals_known` is fuel already on the road. `supply(k,f,t)` is the scheduled delivery list from the snapshot, with delays and shortfalls already applied.

**Why this is a linear programme, not an integer one.** Quantities are continuous litres, and every constraint is linear. There is no yes-or-no decision, so no integer variables are needed: the solution is optimal and fast, and a mixed-integer solver would add a dependency and solve time for nothing. Integer behaviour (a minimum useful shipment of 500 L, rounding to whole litres) is applied after solving by a deterministic rounding step. If a minimum-lot rule ever has to be part of the optimisation, that is the point to add integer variables (ADR-06).

**Size.** About 12 station-fuel pairs, 18 route-fuel pairs and 6 depot-fuel pairs over 48 ticks is roughly 2,300 variables and a similar number of constraints. HiGHS (through SciPy `linprog`) solves this in milliseconds to a few tens of milliseconds. `[inferred]`, to be measured and reported.

**Commit rule.** Only the shipments at `t = 0` are executed (as proposals). The rest of the plan is discarded and recomputed at the next snapshot. This is what makes the policy robust to events that change the world mid-plan.

**Rounding and posting.** Quantities are rounded down to whole litres and shipments under `min_shipment` are dropped. Each remaining shipment becomes a proposal line.

### Fallback chain

```
LP  --(infeasible, timeout over 1 s, or exception)-->  greedy  --(exception)-->  rules  --(exception)-->  no recommendation, with the reason shown
```

`rules` is the simplest policy: for each `CRITICAL` pair, send `min(route max, headroom)` on the fastest open route. Each step down is counted (`fallback_activations_total`) and shown on the proposal.

## 6.5 Anomaly detection

Purpose: tell the operator when demand departs from what the model expects, and whether the departure is understood.

```
z(s,t) = (demand_total(s,t) - forecast_total(s,t)) / sigma_total(s,t)       station level, all fuels summed
flag   = at least 2 of the last 3 ticks have abs(z) > 3, using the median and MAD of recent z to avoid being fooled by outliers
```

| Class | Condition | Shown as |
|---|---|---|
| **Explained** | An event covering this station explains the change: a spike (`demand_multiplier` raised), an outage, or a scheduled supply change | "Demand up 1.7x: demand spike event, started tick 8" |
| **Unexplained** | A flag with no event | "Demand 40% above forecast, no known cause" and the confidence of proposals for that station is lowered |

Two non-demand checks use the same machinery: a delivery that arrives later than its planned tick without a `shipment_delay` event, and an allocation that delivers less than its `executed_quantity` (fuel lost to headroom).

## 6.6 Explanation card

A recommendation is only useful if the operator can see why. Each proposal carries:

| Part | Content |
|---|---|
| **Headline** | Station, fuel, hours to stockout at P50 and P90, risk level |
| **Action** | Depot, route, litres, expected arrival tick |
| **Effect** | Risk before and after, unmet litres avoided over the horizon |
| **Signals** | Current stock, expected demand to arrival, stock on the road, active events, scheduled supply |
| **Binding constraints** | What stopped it from doing more: route maximum, depot stock, dispatch capacity, headroom |
| **Alternatives** | At least: do nothing, the second-best route or depot, wait one tick. Each with its effect, computed by the same estimator |
| **Confidence** | `High`, `Medium` or `Low` with the reasons |

## 6.7 Confidence

A score from 1.0 reduced by the conditions below, then bucketed: `High` at 0.8 or above, `Medium` at 0.5 or above, otherwise `Low`. Low-confidence proposals are flagged for human review rather than shown as certain. The weights are initial values.

| Condition | Reduction |
|---|---|
| Less than one day of demand history (cold start) | 0.30 |
| Unexplained anomaly active for this station | 0.20 |
| Snapshot stale, inconsistent or older than 2 ticks | 0.20 |
| Allocator fell back from the LP | 0.10 |
| Recent forecast coverage outside 75% to 85% | 0.10 |

## 6.8 Evaluation: the replay benchmark

The simulator is deterministic, so the same crisis can be given to every policy. This is the evidence that the intelligence works, and the only source of numbers for the README.

| Item | Definition |
|---|---|
| Scenarios | `baseline`, `demand_spike`, `supply_disruption`, `final_combined` (events replayed through `/admin/events`) |
| Horizon | 192 ticks (two simulated days) |
| Policies | `none`, `rules`, `greedy`, `lp` at P50, P80 and P90 |
| Headline metric | Service level from `/v1/metrics` (the simulator's own) and unmet litres |
| Safety metrics | Failed allocations; litres lost to headroom (sent minus received); allocations rejected |
| Operations metrics | Stockout station-hours (ticks with unmet demand x `tick_minutes` / 60, from demand history); litres shipped; policy decision time p95 |
| Reproducibility | A hash of the final demand history is stored with each result; a re-run must reproduce it |
| Fairness | Same replay, same seed, same horizon, a freshly reset simulator for every policy |

Reference point already measured: with no actions, service level is 0.46 on the baseline. The benchmark's job is to show how far each policy rises above it, and where it still loses fuel.
