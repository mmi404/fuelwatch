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
2. Sort by hours_p90 ascending (most urgent first). Skip OK pairs, UNKNOWN pairs (no forecast) and
   stations in outage.
3. For each remaining (s, f), and for the route being tried (arrival = T + tau):
     I75      = projected stock at arrival on the P75 demand path, with fuel already on the road
     I25      = the same on the P25 demand path (more stock left, so less headroom)
     D75      = P75 demand over the cover window after arrival (cover = 24 ticks initially)
     need     = D75 - I75 - inbound arriving inside the window
     need     = min(need, cap - I25)                       # headroom: never more than fits
     floor    = min(min_shipment, half of D75)             # slow fuels: cover can be < min_shipment
     if need < floor: the pair is covered
4. Routes: only usable ones (rules 1-4), shortest transit first, ties to the depot with more stock of f.
5. quantity = whole litres of min(need, m_r, depot stock - reserve, dispatch capacity left at the depot).
   Dispatch left already subtracts allocations created this tick that the snapshot shows.
6. Place the shipment, then recompute the need (the shipment is now inbound) and repeat, moving to
   another route when a depot cannot serve it, up to 6 lines per pair.
7. Record which constraint limited each shipment (need, route_max, depot_stock, dispatch_capacity or
   headroom) and, when a need goes unmet, the shortfall and its reason, for the explanation.
```

The P25 and P75 paths come from the same cumulative quantiles as the stockout estimator, taken at `+/- 0.6745` standard deviations. Using the low-demand path for headroom and the high-demand path for need is what makes overflow impossible without being timid about stock. The first version used a flat 1,000 L minimum shipment and left slow-moving pairs (Tongi octane at about 35 L per tick, whose whole cover is 840 L) dry for up to 43 ticks; the relative floor fixed that.

Rule 4 in practice: a POST at tick `T` departs in the processing of tick `T`, so only a disruption covering tick `T` can fail it (end tick inclusive); one that starts later cannot, because the route is checked at departure only.

Measured on the live simulator, driving the allocator every tick for 192 ticks and posting its shipments (`[live]`): with no events, 128 shipments and 113,848 L gave a service level of 1.000 (no action gives 0.46), with every POST accepted, no `FAILED` allocation and no litres lost at arrival (checked by conservation of stock, served and delivered litres). With the `final_combined` events replayed (a 1.7 times spike in Dhaka, an 8-tick Gazipur supply delay, a 10-tick disruption of `route-gazipur-mirpur`) the result was the same: 130 shipments, service level 1.000, no rejections, no failures, no loss. These runs are one seed, one scenario and no faults; the replay benchmark with fault injection and the LP policy is where the comparison is made properly.

### Policy 2: rolling-horizon linear programme (the main policy)

Greedy decides one station at a time, so it can spend a depot's dispatch capacity on one station while another, more critical, one waits. The LP decides all stations together.

Decision epoch `T`, horizon `H` ticks (48 initially, 12 simulated hours), relative ticks `t = 0 .. H-1`.

| Symbol | Meaning |
|---|---|
| `x(r, f, t) >= 0` | Litres shipped on route `r`, fuel `f`, at relative tick `t` (the decision) |
| `u(s, f, t) >= 0` | Unmet demand |
| `I(s, f, t)` | Station inventory after tick `t` |
| `J(k, f, t)` | Depot inventory when the shipments of tick `t` are posted |
| `g(k, f, t) >= 0` | Scheduled supply refused for lack of depot room |
| `w(s, f, t) >= 0` | Fuel that would not fit in the tank on arrival (a slack, so the programme always has a solution) |
| `z(s, f, t) >= 0` | How far stock after tick `t` is below the buffer (soft) |

```
minimise   sum over s,f,t of  u(s,f,t)                        unmet litres (the score the simulator reports)
         + eps_ship   x sum of x(r,f,t)                        small cost, prevents pointless shipping
         + eps_lead   x sum of tau_r x x(r,f,t)                prefer fast routes among equals
         + c_waste    x sum of g(k,f,t)                        do not let a full depot refuse a delivery
         + c_buffer   x sum of z(s,f,t)                        keep a buffer of stock at every tick, not only at the end
         + c_hold     x sum of I(s,f,t)                        small cost of stock sitting in a tank, so shipping is not earlier than needed
         + c_overflow x sum of w(s,f,t)                        very expensive: nothing should be lost at arrival

subject to
  station balance   I(s,f,t) = I(s,f,t-1) + arrivals_known(s,f,t) + sum over r into s of x(r,f,t - tau_r) - (d_q(s,f,t) - u(s,f,t))
  no loss           I(s,f,t-1) + arrivals_known(s,f,t) + sum x(r,f,t - tau_r) - w(s,f,t)  <=  cap(s,f)
  demand bound      u(s,f,t) <= d_q(s,f,t),   and  u = d_q  while the station is in outage
  depot balance     J(k,f,t+1) = J(k,f,t) - sum over r from k of x(r,f,t) + supply(k,f,t) - g(k,f,t),   J <= cap_depot
  depot stock       sum over r from k of x(r,f,t)  <=  J(k,f,t)                              (the POST-time check, rule 6)
  dispatch          sum over r from k, all f of x(r,f,t)  <=  dispatch_capacity(k)        for every t
  route limit       x(r,f,t)  <=  m_r
  blocked           x(r,f,t) = 0   if route r is covered by a disruption at T+t, or the destination is in outage
  buffer            I(s,f,t) + z(s,f,t)  >=  min(demand of the next B ticks at d_q, 0.85 x cap(s,f))        B = 24 ticks, like the greedy cover
  initial state     I(s,f,-1) = current inventory;  J(k,f,0) = current depot inventory
```

`d_q` is the demand path at the chosen risk quantile (P80). `arrivals_known` is fuel already on the road. `supply(k,f,t)` is the scheduled delivery list from the snapshot. The simulator applies a `shipment_delay` or `supply_shortfall` to the supply rows when the event starts, so rows already changed are used as they are, and an event that has not started yet is applied to the rows it will still find waiting. This is the one place the LP sees something the greedy policy does not: a delivery that is about to be delayed or cut.

A shipment whose arrival falls after the horizon has no variable, since it cannot change anything inside it. A blocked route or a closed destination fixes the variable at zero for that tick. For the shipment that is posted now, a destination in `OUTAGE` is closed; for later ticks the outage events say which ticks are closed.

Weights, in the units of the objective (model units are thousands of litres): unmet 1, buffer 0.2, holding 0.002, shipping 0.01 plus 0.002 per tick of transit, depot waste 0.05, overflow 5. They only need to keep the order *unmet demand, then buffer, then nothing pointless*. They were set once by reasoning and checked against the benchmark, not tuned on it.

**Why this is a linear programme, not an integer one.** Quantities are continuous litres, and every constraint is linear. There is no yes-or-no decision, so no integer variables are needed: the solution is optimal and fast, and a mixed-integer solver would add a dependency and solve time for nothing. Integer behaviour (a minimum useful shipment of 500 L, rounding to whole litres) is applied after solving by a deterministic rounding step. If a minimum-lot rule ever has to be part of the optimisation, that is the point to add integer variables (ADR-06).

**Size.** About 12 station-fuel pairs, 18 route-fuel pairs and 6 depot-fuel pairs over 48 ticks is roughly 2,300 variables and a similar number of constraints. HiGHS (through SciPy `linprog`) solves this in milliseconds to a few tens of milliseconds. `[inferred]`, to be measured and reported.

**Commit rule.** Only the shipments at `t = 0` are executed (as proposals). The rest of the plan is discarded and recomputed at the next snapshot. This is what makes the policy robust to events that change the world mid-plan.

**Rounding and posting.** The shipments of tick 0 only are taken, and only for pairs that are not `OK`, not `UNKNOWN` and not in outage, the same pairs the greedy policy acts on. Each is then clipped, in whole litres rounded down, to the route maximum, the depot stock left, the dispatch capacity left and the tank headroom at arrival on the low-demand path (the same headroom rule 8 uses for the greedy policy, since the LP plans on the P80 path and a lower real demand must not overflow the tank). The size that was limiting is recorded as `binding`. A line below the relative minimum shipment (the same floor as the greedy policy) is dropped.

### Fallback chain

```
LP  --(infeasible, timeout over 1 s, or exception)-->  greedy  --(exception)-->  rules  --(exception)-->  no recommendation, with the reason shown
```

`rules` is the simplest policy: for each `CRITICAL` pair, send `min(route max, headroom, depot stock, dispatch left)` on the fastest open route, if that is at least 500 L. Each step down is counted (`fuelwatch_fallback_activations_total{component="allocator"}`), logged as `allocator.fallback`, stored as the proposal's `policy`, and lowers its confidence by 0.10. The LP's solve time limit is 1 s (`time_limit`), enforced by HiGHS. The policy to start from is the `ALLOCATOR` setting: `lp` (default), `greedy` or `rules`.

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
| **Effect** | Risk before and after, hours to stockout before and after, and the unmet litres avoided: the estimator is run again with the proposed shipments added, and the P90 shortfall over the next 48 ticks is compared |
| **Signals** | Current stock, expected demand to arrival, stock on the road, active events, scheduled supply |
| **Binding constraints** | What stopped it from doing more: route maximum, depot stock, dispatch capacity, headroom |
| **Alternatives** | Do nothing, the same shipments one tick later, and the fastest other usable route with stock (when there is one). Each with its outlook, computed by the same estimator |
| **Confidence** | `High`, `Medium` or `Low` with the reasons |

## 6.7 Confidence

A score from 1.0 reduced by the conditions below, then bucketed: `High` at 0.8 or above, `Medium` at 0.5 or above, otherwise `Low`. Low-confidence proposals are flagged for human review rather than shown as certain. The weights are initial values.

| Condition | Reduction |
|---|---|
| Less than one day of demand history (cold start) | 0.30 |
| Unexplained anomaly active for this station | 0.20 |
| Snapshot stale, inconsistent or older than 2 ticks | 0.20 |
| Allocator fell back from the LP | 0.10 |
| Recent forecast coverage outside 72% to 85%, once at least 200 forecasts are scored | 0.10 |

## 6.8 Evaluation: the replay benchmark

The simulator is deterministic, so the same crisis can be given to every policy. This is the evidence that the intelligence works, and the only source of numbers for the README.

| Item | Definition |
|---|---|
| Scenarios | `baseline`, `demand_spike`, `supply_disruption`, `final_combined` (events replayed through `/admin/events`) |
| Horizon | 192 ticks (two simulated days) |
| Policies | `none`, `rules`, `greedy`, `lp` (P80 planning path) |
| Headline metric | Service level from `/v1/metrics` (the simulator's own) and unmet litres |
| Safety metrics | Failed allocations; litres lost to headroom (sent minus received); allocations rejected |
| Operations metrics | Stockout station-hours (ticks with unmet demand x `tick_minutes` / 60, from demand history); litres shipped; policy decision time p95 |
| Reproducibility | A hash of the final demand history is stored with each result; a re-run must reproduce it |
| Fairness | Same replay, same seed, same horizon, a freshly reset simulator for every policy |

Reference point: with no actions, service level is 0.46 on the baseline. The benchmark's job is to show how far each policy rises above it, and where it still loses fuel.

### Measured results

The runner is `python -m fuelwatch.bench` (`backend/fuelwatch/bench.py`). It refuses to reset the simulator on port 8000, so it runs on a second instance on port 8001 started from the same image. Each run begins with fault clearing, a pause and a reset, replays the scenario events through `/admin/events`, and then for every tick reads the world through the live client, trains the forecaster on the newest complete history ticks the way the live system does, asks the policy for shipments, posts them with their own keys, and steps. The policies are the same code the live system runs: `none`, `rules`, `greedy` and `lp` (called through the fallback chain, so a failure would show in the `Fallbacks` column; there were none). All runs use horizon 192 ticks and simulator seed 12345. The LP plans on the P80 path only; the P50 and P90 variants named above were not run.

**The four simulator scenarios.** Saved in `bench/results/20261125-221217/` (`none`) and `bench/results/20261125-232153/` (`greedy`, `lp`; raw allocations and history hashes in each `runs/` folder):

| Scenario | Policy | Service level | Unmet L | Shipments | Litres shipped | Rejected POSTs | Failed allocations | Lost at arrival L | Stockout pair-hours | Policy p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | none | 0.461 | 100,452 | 0 | 0 | 0 | 0 | 0 | 291.5 | n/a |
| baseline | greedy | 1.000 | 0 | 128 | 113,848 | 0 | 0 | 0 | 0 | 3.5 |
| baseline | lp | 1.000 | 0 | 130 | 115,120 | 0 | 0 | 0 | 0 | 182 |
| demand_spike | none | 0.454 | 103,333 | 0 | 0 | 0 | 0 | 0 | 299.25 | n/a |
| demand_spike | greedy | 1.000 | 0 | 130 | 115,758 | 0 | 0 | 0 | 0 | 3.5 |
| demand_spike | lp | 1.000 | 0 | 133 | 118,171 | 0 | 0 | 0 | 0 | 182 |
| supply_disruption | none | 0.461 | 100,452 | 0 | 0 | 0 | 0 | 0 | 291.5 | n/a |
| supply_disruption | greedy | 1.000 | 0 | 128 | 113,848 | 0 | 0 | 0 | 0 | 3.5 |
| supply_disruption | lp | 1.000 | 0 | 130 | 115,120 | 0 | 0 | 0 | 0 | 174 |
| final_combined | none | 0.453 | 103,916 | 0 | 0 | 0 | 0 | 0 | 300.25 | n/a |
| final_combined | greedy | 1.000 | 0 | 130 | 116,043 | 0 | 0 | 0 | 0 | 3.6 |
| final_combined | lp | 1.000 | 0 | 134 | 118,915 | 0 | 0 | 0 | 0 | 174 |

These four are gentle: depots start with plenty of stock, so greedy already serves everything and the LP cannot do better. The `supply_disruption` scenario changes nothing for either policy because the delayed and reduced depot deliveries never mattered while depot stock stayed high. A second run of `final_combined` with `greedy` on the earlier run reproduced the identical demand-history hash and service level.

**Four harder worlds of our own** (`stress_*` in `bench.py`, built from the same event types: they squeeze depot supply, routes and tanks together). Saved in `bench/results/20261125-230240/`:

| Scenario | What happens | Policy | Service level | Unmet L | Shipments | Litres shipped | Lost at arrival L | Stockout pair-hours | Policy p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| stress_crunch | demand x3 everywhere for 80 ticks, all supply cut to 20%, deliveries delayed 10 ticks | none | 0.243 | 267,302 | 0 | 0 | 0 | 461 | n/a |
| | | rules | 0.985 | 5,200 | 51 | 281,372 | 0 | 15.25 | 1.5 |
| | | greedy | 0.989 | 3,982 | 263 | 274,349 | 0 | 11 | 4.8 |
| | | lp | **0.995** | 1,856 | 264 | 274,851 | 0 | 6.25 | 228 |
| stress_roads | two routes down for 40 ticks, a third for 20, demand x2.2 | none | 0.363 | 150,393 | 0 | 0 | 0 | 421 | n/a |
| | | rules | 1.000 | 0 | 34 | 211,855 | 0 | 0 | 0.4 |
| | | greedy | 1.000 | 0 | 181 | 163,821 | 0 | 0 | 4.1 |
| | | lp | 1.000 | 0 | 183 | 165,031 | 0 | 0 | 187 |
| stress_outage_rebound | two stations closed for 12 ticks, then demand x2.5, supply cut to 30% | none | 0.362 | 151,624 | 0 | 0 | 0 | 415.25 | n/a |
| | | rules | 0.972 | 6,608 | 29 | 188,392 | 0 | 19.5 | 0.4 |
| | | greedy | 0.972 | 6,608 | 174 | 157,276 | 0 | 19.5 | 3.9 |
| | | lp | 0.972 | 6,608 | 174 | 158,983 | 0 | 19.5 | 194 |
| stress_everything | demand x2.6, supply cut to 25%, deliveries delayed 12 ticks, two routes down for 30 ticks, one station closed for 8 | none | 0.303 | 197,535 | 0 | 0 | 0 | 452.5 | n/a |
| | | rules | 0.969 | 8,709 | 40 | 229,771 | 49 | 12.5 | 0.5 |
| | | greedy | 0.970 | 8,379 | 206 | 201,049 | 0 | 10.25 | 4.1 |
| | | lp | **0.981** | 5,374 | 210 | 206,125 | 0 | 6.75 | 211 |

No run had a rejected POST or a failed allocation. The shape of the result:

1. Every policy is far above doing nothing (0.24 to 0.36 there).
2. The LP is never worse than greedy in these eight runs, and better in the two worlds where depot stock is scarce: 0.995 against 0.989 in `stress_crunch` (unmet fuel more than halved, 1,856 L against 3,982 L) and 0.981 against 0.970 in `stress_everything` (5,374 L against 8,379 L). In `stress_outage_rebound` the 6,608 L unmet is identical for all three policies, which fits fuel that nobody could have delivered because the station was closed.
3. The gain is small in absolute terms: about one percentage point of service level at most. The honest reading is that greedy is already a strong policy on this simulator, and the LP earns its keep only when depot supply is the limit.
4. The simplest `rules` policy is better than expected: it matches greedy in `stress_outage_rebound` and `stress_roads` and loses at most 0.4 points elsewhere, with 49 L lost at arrival in one world. It ships large lots (fewer shipments, more litres), which is why it is a sound last fallback.
5. The LP's time is about 190 to 230 ms at p95 per decision against 4 ms for greedy. That is well inside the 1 s limit and the 500 ms poll cycle's slack, but it is fifty times slower, which is why the chain keeps greedy as the fallback.

On the live simulator (port 8000, baseline, autopilot on, `lp` policy), 130 ticks gave a service level of 1.000, no failed allocation and no fallback; assessment plus LP took 159 ms on average per snapshot (`[live]`).

Limits that remain: one simulator seed and one run per cell (no confidence intervals); no injected faults in the benchmark; the weights of the LP were set by reasoning and not tuned, so a better weighting may exist; and the stress worlds are our own, chosen to be hard, not the simulator's scenario files.
