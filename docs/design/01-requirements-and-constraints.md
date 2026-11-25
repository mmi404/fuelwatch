# 1. Requirements and constraints

## 1.1 The task

The brief asks for an intelligent fuel management application that connects to the provided simulator. It must display fuel supply information, identify potential shortages, provide intelligent allocation recommendations, and handle system failures, and it must be a working, demonstrable application at the deadline (Thursday 26 November 2026).

Those four verbs are the functional core. The hackathon also scores architecture, DevOps, resilience, observability and the demo, so non-functional requirements are part of the product, not an afterthought.

## 1.2 Functional requirements

| ID | Requirement | Acceptance check |
|---|---|---|
| FR-1 | **Display supply information.** Stock and capacity for every station and fuel; depot stock; routes and their status; shipments on the road; scheduled and delayed supply; active and scheduled events. Every value shows the tick it belongs to and its age. | Dashboard grid matches `GET /v1/stations`, `/v1/depots` and `/v1/routes` at the same tick. |
| FR-2 | **Identify potential shortages.** For each station and fuel: a stockout estimate in hours at P50 and P90, a risk level, and an inbox of alerts ranked by urgency. | On a replay of the no-action baseline, an alert fires for Tongi diesel well before tick 65 (its first unmet demand). |
| FR-3 | **Recommend allocations.** Proposals that the simulator will accept: valid route, depot stock, dispatch capacity, destination headroom, route limit, no closed station, no disrupted route. Each comes with an explanation: signals, binding constraints, alternatives including do nothing, expected effect, confidence. | 100% of proposals executed without a validation 409 on a fresh snapshot. |
| FR-4 | **Act on a recommendation.** A human approves; the system re-validates, sends an idempotent request, tracks the allocation to arrival, and keeps an audit trail. An optional autopilot mode executes within guard limits. | An approved proposal reaches `ARRIVED` in the simulator; one audit row per state change; no duplicate allocation after a retry. |
| FR-5 | **Handle system failures.** Survive each injected fault (latency, error rate, stale data, unavailable, stream disconnect), malformed data, simulator restart and simulator reset, and failures of our own components, keeping the operator informed. | The chaos drills in part 8 pass; the UI shows degraded state and snapshot age during each. |
| FR-6 | **Prove the intelligence.** A replay benchmark runs the same crisis under several policies and reports service level, unmet litres and failed allocations. | Greedy and LP beat do-nothing (0.46) on `final_combined`, from identical replays. |
| FR-7 | **Be operable.** Liveness and readiness probes, metrics, structured logs, one-command start. | `docker compose up` brings the whole system to ready. |

## 1.3 Non-functional requirements

These are targets. A target is not a result; each is checked by the method in the last column and reported in part 10 only after it is measured.

| ID | Quality | Target | How it will be checked |
|---|---|---|---|
| NFR-1 | Freshness | Snapshot age at most 2 seconds in normal operation | `snapshot_age_seconds` metric during a run |
| NFR-2 | Dashboard read speed | p95 under 100 ms for `/api/snapshot`, served from memory | k6 open-model test against our API |
| NFR-3 | Load on the simulator | At most 30 requests per second, about 40% of the lowest measured capacity (60 to 76) | Request counter on the client over a run |
| NFR-4 | Recovery | Back to live data within 10 seconds of a fault clearing, with no manual step | Chaos drill with timestamps |
| NFR-5 | Write safety | Zero duplicate allocations and zero allocations lost to a known-avoidable cause (closed route, no headroom) | Replay benchmark counters; audit table |
| NFR-6 | Degraded honesty | During any fault the UI shows the data age; execution is blocked when data is too old | Drill checklist |
| NFR-7 | Start up | Fresh clone to ready in under 5 minutes, one command | Timed clean start |
| NFR-8 | Determinism of proof | Benchmark results reproduce exactly on a re-run | Run twice, compare hashes |
| NFR-9 | Bounded observability cost | Metric label values come only from fixed enums and entity ids; series count is flat over time | Series count check in tests |

## 1.4 Constraints the simulator imposes

Each row is a fact about the simulator and the design consequence we drew from it. These are why the design looks the way it does.

| Constraint | Source | Consequence in the design |
|---|---|---|
| Stations never refill on their own; with no action service level is 0.46 over 192 ticks and Tongi diesel runs out at tick 65. | `[live]` | Recommendations run continuously, not only when an event arrives. |
| Demand per tick is `daily_liters / 96 x hour_factor x region x multiplier x noise`, validated on 1,572 rows. | `[code]` `[live]` | A structural forecaster with learned hour bands beats a generic time-series model on a few days of data. |
| Fuel reaches a station only through allocations; depots get fuel only from scheduled supply arrivals. | `[code]` | Two flows to model: supply into depots (scheduled, can be delayed) and allocations out to stations (ours). |
| Depot stock is debited at POST; a route disrupted before departure fails the allocation and does not refund. | `[live]` | Read scheduled events before posting; avoid routes about to be disrupted. |
| Station receives `min(headroom, quantity)`; the rest is lost. Destination check ignores fuel in transit. | `[live]` | The allocator computes headroom net of what is already on the road. |
| Dispatch capacity applies per POST tick to allocations created in that tick. | `[live]` | One tick's shipments from a depot are planned together, not one at a time. |
| Fuel arrives in the processing of tick `created + transit_ticks`, before that tick's demand, and shows as `ARRIVED` `transit_ticks + 1` steps after the POST. | `[code]` `[live]` | Projections add arrivals at `expected_arrival_tick`, ahead of demand in the same tick; the UI must not claim arrival one step early. |
| The stream sends `simulation.tick` every tick, but nothing for departure, arrival, failure or station stock; slow clients are dropped silently. | `[code]` `[live]` | Poll REST; use the stream only as a latency hint; watch tick freshness. |
| The simulator handles about 60 to 76 requests per second and reset a connection at 50 concurrent. | `[live]` | Single poller; the browser never talks to the simulator; load tests target our API. |
| Real speed is about 4.6 ticks per second, not 8. | `[live]` | An approval of 30 seconds is about 140 ticks; approval must be an upper bound, re-validated. |
| Faults hit all of `/v1/*` including POST; `/v1/health` and `/admin/*` bypass faults. | `[live]` | The simulator's health endpoint cannot tell us it is usable; we judge usability from our own reads. |
| `stale_data` only adds a header; the data is not actually stale. | `[live]` | Treat the header as the signal and label values stale. |
| State persists across container restarts; only `/admin/reset` returns to tick 0. | `[live]` | Detect resets by tick regression; offer a clear reset path before benchmarks. |
| Tick length and speed differ between the image defaults and the guide's compose file. | `[live]` | Read `tick_minutes` at runtime; test at both settings. |

## 1.5 Out of scope

- Real fuel infrastructure, real data, multi-tenant security. The simulator has no authentication and neither does our read path; only write and control endpoints use a shared operator token.
- Reinforcement learning. A heuristic and an LP are explainable and benchmarked; RL would have to beat them on the replay benchmark first.
- Changing the simulator. We use only its public API and its admin endpoints.

## 1.6 How the design maps to the judging criteria

| Criterion | Weight | Where the design answers it |
|---|---|---|
| Product and user experience | 20% | FR-1 to FR-4; explanation cards; risk inbox; degraded-state banner (part 5.4, part 7) |
| Intelligence | 20% | Part 6: forecaster, stockout estimate, allocation, anomaly detection, replay benchmark |
| Architecture | 15% | Part 2 layering rules; part 9 decision log |
| DevOps | 15% | Compose deployment (part 2.5), one command start, CI plan (part 10) |
| Resilience | 15% | Part 8: failure modes, retries, breaker, degradation ladder, chaos drills |
| Observability and performance | 10% | Part 8: metrics, SLOs, logs; k6 plan |
| Demo | 5% | A scripted run that exercises every requirement (part 10) |
