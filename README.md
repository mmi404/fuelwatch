# FuelWatch: Fuel Supply Intelligence & Resilience Platform

**An operations-centre console that sees a fuel shortage coming, recommends who should ship what to whom, explains why, and keeps working when the systems underneath it fail.**

Built for the BUP CSE FEST 2026 hackathon on top of the organizer-provided BUP Fuel Supply Simulator. Python (FastAPI) backend, Next.js dashboard, Docker Compose, Prometheus and Grafana, k6.

> **Status: early implementation.** The simulator ingest layer exists under `backend/` (client, models, snapshot, poller, health probes) together with its resilience layer (per-request retries with a budget, a concurrency cap, circuit breakers, the LIVE/DEGRADED/STALE/STARTING mode and one error shape), plus SQLite persistence of world epochs, reset detection and the demand history, an online-learned demand forecaster and a stockout estimator served at `/api/risks` and `/api/forecast`, a rules-and-greedy allocator, and proposals with explanation cards and stockout alerts stored in SQLite and served at `/api/proposals` and `/api/alerts`, and approval and rejection with an execution gate, re-validation, idempotent sending, a ledger and an audit log, behind `X-Operator-Token`, an autopilot with guard limits, demo controls proxied to the simulator, a ledger, audit and status API, the simulator's tick stream used as a refresh hint, and a server-sent events stream for the browser, with unit tests. A replay benchmark runner (`python -m fuelwatch.bench`) compares the no-action and greedy policies on four scenarios; its saved results are in [bench/results](bench/results) (one seed, no injected faults). Everything else described here is design and is not built yet. The simulator's behaviour is documented in the [findings](docs/SIMULATOR_STATUS_FINDINGS.md) and the whole system is designed in [docs/design](docs/design/README.md) (also as a [PDF](docs/design/FuelWatch-Engineering-Design.pdf)). Commands, ports and every number marked TBD are intended or unmeasured.

<!-- TODO before submission: replace with a real screenshot or a 20-second GIF of the dashboard during the final_combined crisis. -->
![Dashboard](docs/images/dashboard.png)

---

## The problem

Fuel moves through a chain: import, port, depot, route, station, customer. A delayed shipment drains a depot, a demand spike empties a station, and a closed road makes a perfectly good allocation impossible. Operators have minutes to decide and little visibility.

The simulator makes the stakes concrete (see the [findings](docs/SIMULATOR_STATUS_FINDINGS.md#3-key-behaviours)). **If nobody allocates anything, stations never refill: over two simulated days service level falls to 0.46 and about 100,000 litres of demand goes unserved.** The first tank to run dry is Tongi diesel at tick 65, about 16 simulated hours in. Intelligent allocation is not an add-on here; it is the only thing keeping the network supplied.

## What FuelWatch does

| | |
|---|---|
| **See** | One live view of 4 stations x 3 fuels, 2 depots, 6 routes, incoming supply, shipments in transit and active disruptions. Every number carries its tick and age. |
| **Predict** | Per station and fuel: demand forecast with P10 / P50 / P90 bands and an hours-to-stockout estimate that accounts for fuel already on the road. |
| **Detect** | Flags anomalous demand and inventory changes, labelled *explained* (a known event) or *unexplained*. |
| **Decide** | An optimizer proposes shipments that respect every real constraint: route limits, depot stock, dispatch capacity, destination headroom, closed routes. |
| **Explain** | Each recommendation is a card: projected stockout, current stock, expected demand, the action, risk before and after, the signals behind it, binding constraints, alternatives (including *do nothing*), confidence. |
| **Act** | A human approves an upper bound; the platform re-validates against fresh state (the quantity can only shrink), then submits with an idempotent request. An optional autopilot mode acts within guard limits. Full audit trail. |
| **Survive** | Cached snapshots, budgeted retries, circuit breakers, detection of simulator resets, and a fallback for every intelligent component. |

```
ALERT  Mirpur Fuel Station · Diesel
Projected stockout   P50 6.2 h   P90 4.1 h
Current inventory    8,400 L       Expected demand 11,900 L
Recommended          5,000 L  Gazipur Depot -> Mirpur   (arrives in 3 ticks)
Risk if no action    72%    Risk after action  19%
Alternatives         split with Patiya (+4 ticks) · wait one tick · do nothing
Confidence           High (forecast coverage 91%, snapshot 1 tick old)
```
*(Illustrative layout of the explanation card.)*

## Architecture

```
          BUP Fuel Supply Simulator   (REST /v1 · SSE /v1/stream · /admin)
                      │   single poller, once per tick
                      ▼
 ┌──────────────────── FuelWatch backend (FastAPI) ────────────────────┐
 │ Ingest      poller + SSE watchdog + schema validation               │
 │ Snapshot    immutable WorldSnapshot, swapped atomically             │
 │ Intelligence  forecaster → stockout estimator → anomaly detector    │
 │ Decision    LP optimizer → greedy fallback → proposal ledger       │
 │ Execution   approve → idempotent POST, retry budget, breakers       │
 │ Ops         /livez /readyz /metrics · JSON logs · audit table       │
 └───────────┬─────────────────────────────────┬───────────────────────┘
             │ REST + SSE from cache           │ /metrics
             ▼                                 ▼
     Next.js operator dashboard        Prometheus → Grafana
```

Four of the design rules, each earned from testing the simulator (full evidence in [docs/SIMULATOR_STATUS_FINDINGS.md](docs/SIMULATOR_STATUS_FINDINGS.md); the complete set, with architecture, data model, flows and state machines, is in [docs/design](docs/design/README.md)):

1. **Browsers never touch the simulator.** It handles roughly 60 to 76 requests per second and dropped a connection under 50 concurrent clients. The dashboard reads our cache; only an approved action writes.
2. **REST is truth, SSE is a hint.** The stream announces ticks but stays silent when a shipment departs, arrives or fails, or when a station changes. We poll REST, so a stream failure costs only latency.
3. **A live connection is not a live stream.** A slow consumer is dropped from the broadcast while its connection stays open on keepalives, so we watch tick freshness instead.
4. **Time is read, never assumed.** Tick length comes from the simulator at runtime, and a tick that goes backwards is treated as a world reset.

## The intelligence

- **Forecast.** A structural demand model (hour-of-day level x active demand spike x a safety multiplier) learned online from the demand history and checked against the simulator's own generator on 1,572 observations. Bands come from the residuals, on cumulative demand. Seasonal-naive sits behind it as a fallback.
- **Stockout estimate.** A tick-by-tick projection of inventory with scheduled supply and in-transit shipments added at their expected arrival and demand subtracted at P50 and P90.
- **Optimizer.** A rolling-horizon linear program that minimises unmet litres, constrained like the simulator's own validation, committing only the first tick and re-solving each snapshot. A greedy cover-based allocator is the fallback and a benchmark policy.
- **Foresight about failure.** The optimizer reads scheduled events and avoids routes about to be disrupted, because a shipment that departs onto a closed route fails and the fuel is not returned.
- **Honest uncertainty.** Low-confidence recommendations are routed to human review instead of being dressed up as certain.

### Proof, not claims

The simulator is deterministic, so every policy is tested on identical replays: reset, inject the same crisis, step the same number of ticks, score.

<!-- TODO: fill from bench/replay output. Do not publish numbers that were not measured. -->

| Policy | Service level | Unmet litres | Failed allocations | Stockout station-hours |
|---|---|---|---|---|
| Do nothing | 0.46 (measured, 192 ticks, baseline) | ~100,000 | 0 | TBD |
| Greedy | TBD | TBD | TBD | TBD |
| LP optimizer | TBD | TBD | TBD | TBD |

Scenario for the headline run: `final_combined`: a Dhaka demand spike (x1.7 for 16 ticks), a Gazipur diesel and petrol shipment delayed 8 ticks, and the Gazipur to Mirpur route disrupted for 10 ticks.

## Built to fail gracefully

| Failure injected | What the platform does |
|---|---|
| Added latency | Serves from cache; the UI never waits on the simulator |
| Random 503s | Retries each failed request up to twice with jitter, within a retry budget; never retries 404, 409 or 422. Measured: the fault fails 24% of requests, so without retries 94% of world reads would fail |
| Simulator unavailable | Breaker opens; dashboard keeps the last snapshot, shows its age, marks proposals provisional |
| Stale-data signal | Detects the stale header; marks every value as stale |
| Stream rejected or silently stalled | Polling does not depend on it; a watchdog reconnects and triggers a full REST resync |
| Malformed fields | Rejects the payload, alerts, keeps the last good snapshot |
| Simulator reset mid-run | Detects the tick going backwards, starts a new world epoch, expires stale proposals |
| Forecaster or optimizer down | Falls back down a documented chain; the fallback is counted and visible |
| Traffic surge | Rate-limited API, bounded queues, cache-served reads |

## Observability

Prometheus metrics for simulator calls, snapshot age, breaker state, allocation outcomes, fallbacks, forecast error and coverage. Structured JSON logs. Separate `/livez` and `/readyz`. The status board distinguishes the simulator being *alive* from being *usable*, because its own health endpoint stays green during injected faults. Alerts use SLO burn rates plus direct alerts for an open breaker, an active fallback and stream loss. Labels are bounded enums only, so cardinality stays flat.

## Load testing

k6 open-model tests (`constant-arrival-rate`, then `ramping-arrival-rate` to find the knee), aimed at FuelWatch, never at the simulator, and repeated under an injected latency fault.

<!-- TODO: fill from loadtest/ report. -->

| Scenario | Rate | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|
| Dashboard reads | TBD | TBD | TBD | TBD | TBD |
| Proposal generation | TBD | TBD | TBD | TBD | TBD |
| Dashboard reads, +latency fault | TBD | TBD | TBD | TBD | TBD |

## Quick start

*Target usage: not available yet.*

Requirements: Docker with Compose.

```bash
cp .env.example .env
docker compose up -d
```

| Service | URL |
|---|---|
| Operator dashboard | http://localhost:3000 |
| Backend API docs | http://localhost:8080/docs |
| Simulator (Swagger) | http://localhost:8000/docs |
| Simulator admin | http://localhost:8000/admin |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |

The simulator starts paused. Press **Run** in the dashboard, or step it manually from the simulator admin page.

<!-- TODO: verify every port and command above against the real docker-compose.yml before submission. -->

### Reproduce the benchmark

```bash
docker compose --profile bench up -d       # second simulator on :8001, so the demo world is untouched
python -m bench.replay --scenario final_combined --policies none,greedy,lp
```

### Reproduce the load test

```bash
k6 run loadtest/dashboard.js
```

### Try the chaos drills

```bash
python -m bench.chaos --fault unavailable --duration 60
python -m bench.chaos --event route_disruption --route route-gazipur-mirpur
```

## Demo script (5 minutes)

1. **Calm.** Network healthy; show the risk inbox, forecast bands and a first proposal card. Approve it.
2. **Crisis.** Inject the `final_combined` events. Watch risk rise, proposals adapt, and the avoided route get excluded.
3. **Failure.** Inject `unavailable`. Dashboard goes to degraded mode with its snapshot age; execution needs an override. Clear it; watch the recovery and resync.
4. **Proof.** Show the replay table and the Grafana burn-rate panel.

## Repository layout

Target layout; so far `backend/` (ingest layer only), `docs/` and `simulator/` exist.

```
backend/        FastAPI service: ingest, snapshot, intelligence, decision, execution, ops
frontend/       Next.js operator dashboard
bench/          replay benchmark and chaos scripts
loadtest/       k6 scripts and report template
observability/  Prometheus rules, Grafana dashboards
docs/           simulator findings, the engineering design (docs/design) and, later, images
simulator/      Compose file for the provided simulator
```

## Documentation

| Document | What it covers |
|---|---|
| [docs/SIMULATOR_STATUS_FINDINGS.md](docs/SIMULATOR_STATUS_FINDINGS.md) | Verified behaviour of the simulator: spec conflicts resolved, the world and its demand model, allocation lifecycle and validation order, faults (with measured error rate), events, SSE limits, determinism and capacity, open questions. The design rules come from it. |
| [docs/design/README.md](docs/design/README.md) | The engineering design, in ten parts: requirements, architecture, data model (ER and class diagrams), flows (sequence and activity diagrams), state machines, algorithms, API contracts, resilience and observability, a decision log with alternatives and evidence, and risks with the build order. Also as a single [PDF](docs/design/FuelWatch-Engineering-Design.pdf). |

## Honest limitations

- The forecaster is fitted to this simulator's demand generator. That is deliberate and documented, but it will not transfer to real data without refitting.
- The simulator's `depot_constraint` event only changes a label and blocks nothing; we display and flag it but cannot make it bind.
- Reinforcement learning is out of scope. If it is ever added, it has to beat the heuristic on the replay benchmark first.
- No real fuel infrastructure is read or controlled; everything runs against the simulator.

## Tech stack

Python 3.12 · FastAPI · httpx · Pydantic · SQLite · SciPy (HiGHS) · Next.js · TypeScript · Tailwind · Docker Compose · Prometheus · Grafana · k6 · GitHub Actions
