# Simulator Findings

What the BUP Fuel Supply Simulator actually does, verified live on 2026-11-20 against image `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`. Method: every endpoint probed, the image's own source and scenario files read (read-only, nothing modified), allocation lifecycle, faults, events and determinism tested.

The simulator for local runs is defined in [`simulator/docker-compose.yml`](../simulator/docker-compose.yml).

**Status vocabulary used below**

- **Checked**: verified by testing the running simulator or reading its source. A checked limitation (for example a cosmetic event) is still a limitation of the simulator; checked only means we have confirmed it.
- **Open**: not yet verified, or not yet taken into the design.

Contents: [1 Spec conflicts](#1-spec-conflicts-checked) · [2 The world](#2-the-world-baseline-seed-12345) · [3 Key behaviours](#3-key-behaviours) · [4 Lifecycle and validation](#4-allocation-lifecycle-and-validation) · [5 Faults and events](#5-faults-and-events) · [6 SSE](#6-sse-is-thinner-than-the-guide-implies) · [7 Capacity and determinism](#7-capacity-and-determinism) · [8 Gap status](#8-gap-status) · [9 Implications](#9-implications-for-the-design)

## 1. Spec conflicts (checked)

| Conflict | Verdict (evidence) |
|---|---|
| Default speed 8 vs 15 | **Image default is 8 ticks/s** (`config.py`: `simulation_speed: float = 8.0`). The guide's compose file overrides it to 15. Measured about 4.6 ticks/s wall-clock on the default container because of per-tick DB and bus overhead, so real throughput is below the nominal value. |
| Default tick 15 vs 8 min | **Image default is 15 min** (`tick_minutes: int = 15`). The guide's compose file overrides it to 8. |
| Which does the judge run? | **Open.** Judges run the published image. Our app must read `tick_minutes` from `/v1/instance` and never hardcode it. |

Two surprises:

- Demand is `daily_liters / 96` per tick, hardcoded for 15-minute ticks. With `TICK_MINUTES=8`, a simulated day holds 180 ticks but only 96 ticks' worth of demand. We pin 15 for realism.
- `POST /admin/reset` set `tick_minutes` back to 15 on an instance started with `TICK_MINUTES=8`. The env-var tick length does not survive a reset.

## 2. The world (baseline, seed 12345)

- 2 regions: Dhaka (factor 1.00), Chattogram (factor 1.08).
- 2 depots: Gazipur (dispatch 12,000 L/tick; diesel 60k, petrol 45k, octane 26k) and Patiya (11,000 L/tick; diesel 55k, petrol 42k, octane 24k).
- 4 stations: Mirpur (urban_high), Tongi (industrial), Karnaphuli (highway), Cox's Bazar (regional). Station tanks hold 6,000 to 18,000 L per fuel, against depot tanks of 40,000 to 90,000 L.
- 6 routes: 4 local (2 to 3 ticks, max 6,000 to 7,000 L) and 2 cross-region (4 ticks, max 5,000 L): Gazipur to Karnaphuli and Patiya to Mirpur.
- 22 scheduled supply arrivals: 4 in the first wave (ticks 12 to 20), then 6 every 64 ticks.
- Demand: per-station daily litres by profile, an hour-of-day curve, regional factor, station multiplier and seeded noise (8% to 12%). Deterministic.

## 3. Key behaviours

1. **Stations never refill on their own.** With zero allocations, service level falls to **0.46 over 192 ticks** (about 100k L unmet). The first unmet demand is **Tongi diesel at tick 65** (about 16 simulated hours), then Karnaphuli diesel at 74, octane at 76 and petrol at 79 (re-measured on a fresh run of 131 ticks with no allocations; an earlier note naming Cox's Bazar as first was wrong). The platform is needed from the start, not only during crises.
2. **Crisis events barely change the unmanaged baseline.** A `final_combined`-style run scored 0.45 against 0.46. The baseline is already stressed, so the intelligence must run continuously.
3. **Idempotency.** Same key and body returns 201 with the existing record. Same key with a different body returns 409 `IDEMPOTENCY_KEY_MISMATCH` and the key stays bound to the first body. **A validation 409 does not consume the key**: `ROUTE_CAPACITY_EXCEEDED` on key `k2`, then `k2` reused successfully.
4. **Two error shapes.** Validation errors are `409 {detail:{code,message}}`, schema errors are `422`. Injected faults are `503 {"error":{"code":"FAULT_INJECTED",...}}`. The client must parse both.
5. **Image ships 4 scenarios** in `/app/scenarios/`: `baseline` (active), `demand_spike`, `supply_disruption`, `final_combined` (seed 9001: Dhaka spike 1.7x for 16 ticks from tick 8, Gazipur diesel and petrol delayed 8 ticks at tick 10, `route-gazipur-mirpur` disrupted ticks 14 to 24). The scenario cannot be switched at runtime, so we **replay `final_combined` events through `/admin/events`**.
6. **Extra endpoints beyond the guide:** `GET /v1/depots/{id}`, `GET /v1/stations/{id}`, `GET /admin/stats`.
7. **`/v1/demand-history`** is a full per-tick, per-station, per-fuel record with `demand`, `served` and `unmet` litres. It is the forecaster's training data. One response returns at most 2,000 rows (`limit` 2001, 5000 and 100000 all returned 2000 at tick 200), which is 166 ticks of 12 rows, so a longer history cannot be read in one call; `station_id` filtering returns all 600 rows of one station at tick 200 (the filtered cap is not measured). An earlier note here said the limit was uncapped; that came from a test at too few ticks to reach the cap. `limit` 0 or -1 silently returns one row. Row ids restart after a reset, so they do not reveal one.

8. **The demand generator is known and checked.** Read from the image source and then validated live: for 1,572 demand-history rows (131 ticks, no allocations) every value lay inside the predicted band and the largest deviation was 12%, which is the highway station's noise setting. The model is `demand = daily_liters / 96 x hour_factor(profile, hour) x region_factor x station_multiplier x (1 + U(-noise, +noise))`, with the noise seeded by (seed, tick, station, fuel).

   | Profile | Hours with the high factor | High | Low | Noise | Daily litres (diesel / petrol / octane) |
   |---|---|---|---|---|---|
   | `urban_high` (Mirpur) | 7 to 10 and 16 to 21 | 1.45 | 0.70 | 10% | 8,500 / 10,500 / 5,600 |
   | `industrial` (Tongi) | 6 to 18 | 1.55 | 0.45 | 8% | 14,000 / 4,500 / 2,200 |
   | `highway` (Karnaphuli) | 6 to 10 and 16 to 21 | 1.35 | 0.75 | 12% | 10,500 / 11,000 / 6,200 |
   | `regional` (Cox's Bazar) | 7 to 21 | 1.25 | 0.65 | 10% | 7,200 / 7,600 / 3,600 |

   The hour is `(tick x tick_minutes / 60) mod 24`, with tick 0 at 00:00. A step function by hour means the average daily factor is below 1 (Mirpur 0.95), so a "daily" figure is not the real daily volume.
9. **Simulator state survives a container restart.** After `docker restart` the simulator came back at the tick it had before (85), paused. Only `POST /admin/reset` returns it to tick 0. Always reset before a benchmark or demo.
10. **`POST /admin/step` advances exactly one tick** and ignores query parameters (`?ticks=2` still moved one tick).
11. **Observed status values** (from source and live): depot `OPEN`, `CONSTRAINED`; station `OPEN`, `OUTAGE`; route `AVAILABLE`, `DISRUPTED`; supply arrival `SCHEDULED`, `DELAYED`, `ARRIVED`; event `SCHEDULED`, `ACTIVE`, `RESOLVED`; instance `PAUSED`, `RUNNING`. A `shipment_delay` moves a supply arrival's `planned_tick` and marks it `DELAYED`. An event created through `POST /admin/events` takes `start_tick` and `duration_ticks`; the stored `end_tick` is their sum.
12. **`/v1/demand-history` returns newest first**, default `limit` 200, filterable by `station_id`.
13. **Event effects in detail** (`[code]`, `engine.py`): a `demand_spike` multiplies the station's visible `demand_multiplier` when it starts and divides it back when it ends (spikes stack), so the multiplier in `/v1/stations` always shows the current spike. `shipment_delay` and `supply_shortfall` change undelivered supply arrivals once, when they start, and are **not undone** when the event ends. A route is checked at departure only, so a disruption after departure does not fail an allocation already on the road.
14. **Supply into a full depot is lost.** A depot accepts `min(room, quantity)` when a scheduled delivery lands, so a depot at capacity wastes the rest.

## 4. Allocation lifecycle and validation

**Lifecycle:** `PENDING` (at POST, **depot debited immediately**; cancelling refunds it) then `IN_TRANSIT` (first tick processed after POST) then `ARRIVED` (departure tick plus `transit_ticks`). `FAILED` (`failure_reason: ROUTE_UNAVAILABLE`) and `CANCELLED` also exist. Example: POST at tick 0 on a 2-tick route showed `IN_TRANSIT` after 1 step and `ARRIVED` after 3 steps, so **the observable lead time is `transit_ticks + 1` steps**. The station receives `min(headroom, quantity)` and the rest is silently lost.

**Validation order at POST** (each is a 409 `detail.code` unless noted): `NOT_FOUND` (404), `ROUTE_MISMATCH`, `DEPOT_CLOSED`, `STATION_CLOSED`, `ROUTE_DISRUPTED`, `ROUTE_CAPACITY_EXCEEDED`, `INSUFFICIENT_INVENTORY`, `DISPATCH_CAPACITY_EXCEEDED`, `DESTINATION_CAPACITY_EXCEEDED`. Quantity of 0 or less and bad enums are 422. Cancelling a non-PENDING allocation is 409 `CANNOT_CANCEL`; an unknown id is 404 `ALLOCATION_NOT_FOUND`.

**Behaviours that cost fuel or correctness if ignored:**

1. **A route disruption between POST and the next tick makes the allocation `FAILED` and the depot is not refunded.** Verified: 5,000 L debited, stayed debited, `allocation_failures` = 1. Mitigation: read `/v1/events` for scheduled disruptions before posting.
2. **Dispatch capacity (12,000 and 11,000 L) is checked per POST tick** against allocations created in the same tick. Verified: 6,500 + 5,000 accepted, +1,000 returned 409, and the next tick accepted again. It is not a rolling limit.
3. **The destination capacity check uses current station inventory only.** Fuel already in transit is not counted, so two in-flight shipments can overshoot and the excess is lost on arrival.
4. Shipments in transit are not exposed as a list. Derive them from `/v1/allocations` (`departure_tick`, `expected_arrival_tick`, `status`).

## 5. Faults and events

**Faults** (verified on `/v1/*` only; `/v1/health` and `/admin/*` bypass them; `duration_seconds` max 3600):

| Fault | Behaviour |
|---|---|
| `latency` | Param `delay_ms`, default 500 (UI hint says 1000). Measured about 0.53 s per request. |
| `error_rate` | Param `rate`, default 0.25 in code (UI hint says 0.3). Returns 503 with `{"error":{"code":"FAULT_INJECTED",...}}`. **Measured on 1,800 requests: 431 failed (23.9%), independently** (two failures in a row 6.2%, expected 6.5%). Because it hits every `/v1/*` request independently, a 10-request refresh fails 97% of the time without retries. |
| `stale_data` | Adds header `X-Simulator-Stale: true`. **The data itself is not stale**, so the header is the only signal. |
| `unavailable` | All `/v1/*` return 503, including POST allocations. |
| `stream_disconnect` | Rejects only **new** `/v1/stream` connections (503 `{detail:{code:FAULT_INJECTED}}`). An already-open stream stays open. The body shape differs from the REST fault shape. |

**Event parameters** (from source): `demand_spike` `{multiplier, station_ids?, region_ids?}`; `route_disruption` `{route_ids}`; `station_outage` `{station_ids}`; `depot_constraint` `{depot_ids}`; `shipment_delay` `{delay_ticks, depot_ids?, fuel_types?}`; `supply_shortfall` `{factor, depot_ids?, fuel_types?}`.

- **`depot_constraint` is cosmetic.** It only sets the depot status to `CONSTRAINED`. The UI hint says dispatch capacity becomes stricter, but the code does nothing; a 5,000 L POST succeeded. We still show and flag it.
- **`station_outage` is functional.** The station status becomes `OUTAGE`, demand is fully unmet, inventory stays frozen, and POSTs to it return `STATION_CLOSED`.

## 6. SSE is thinner than the guide implies

- `simulation.tick` (`{tick, sim_time}`) is sent on every tick, after a `: connected` comment. No replay on reconnect.
- `allocation.status_changed` and `inventory.updated` (depot only) are sent **only at POST or cancel**. There is no event when an allocation departs, arrives or fails, and none for station inventory.
- So the app **polls REST after every tick event**; SSE only says that time moved.
- A client whose 200-slot queue overflows is silently dropped from the broadcast while the HTTP connection stays open with keepalives. **Detect a stalled stream by tick staleness, not connection state.**
- `simulator.notice` is published on reset and on tick errors.

## 7. Capacity and determinism

- **Determinism confirmed.** Two full runs (reset, one injected event, 120 ticks, 12 allocations) produced identical state hashes, including demand history. A replay benchmark is valid.
- **Capacity (laptop, Docker Desktop):** `GET /v1/stations` reached 60 rps single-threaded (p50 15 ms) and 76 rps at 10 concurrent (p50 119 ms, p99 321 ms). A run at 50 concurrent was cut by a connection reset (cause not isolated: simulator or Windows client); the container stayed healthy. The simulator is a small single-process service, so our backend polls it once per tick and serves the dashboard from its own cache. Load tests target our backend, never the simulator.
- **Real tick rate is about 4.6 ticks/s, not 8.** A 30 s human approval is about 140 ticks (about 35 simulated hours), so recommendations must be re-validated at execution time, or the simulation paused during approval.

## 8. Gap status

| Item | Status | Meaning |
|---|---|---|
| Allocation lifecycle | Checked | PENDING, IN_TRANSIT, ARRIVED, FAILED, CANCELLED |
| `depot_constraint` | Checked | Cosmetic, blocks nothing. A simulator limitation that remains. |
| `station_outage` | Checked | Functional; our allocator must avoid closed stations. |
| Dispatch cap | Checked | Per-tick, same-tick allocations only. |
| `stream_disconnect` | Checked | New connections only. |
| Scheduled future events (`/v1/events`) | **Open** | Listing shape known (id, type, start_tick, end_tick, status, parameters). The design uses it as the early-warning signal for route disruptions. |
| `error_rate` exact behaviour | Checked | 23.9% of 1,800 requests failed at the default rate 0.25, independently. |
| 50-way concurrent reset | **Open** | Cause not isolated. Only matters if the simulator is load-tested directly, which our design avoids. |
| Which speed and tick the judges run | **Open** | App reads `tick_minutes` at runtime to stay safe. |

No forecast, risk or recommendation data exists anywhere in the simulator, so all intelligence is ours to build. There is no auth and it is single tenant.

## 9. Implications for the design

1. Run the recommender **continuously**, not only on events; Tongi diesel runs dry at tick 65 with no action.
2. Keep human approval meaningful: pause or step the simulation, or re-validate against fresh state at execution.
3. Primary test harness: replay `final_combined` deterministically (reset, inject, step) and score `service_level` against the no-action baseline.
4. Build the client against the real error shapes, the `X-Simulator-Stale` header, and SSE as a hint only.
5. Pin `SIMULATION_SPEED=8` and `TICK_MINUTES=15` in our compose, but read `tick_minutes` from `/v1/instance` at runtime.
6. Treat a tick that goes **backwards**, or a changed scenario or seed, as a world reset: discard cached demand history and open proposals.
7. Design detail for all of the above is in [design/README.md](design/README.md).
