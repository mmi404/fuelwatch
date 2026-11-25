# 8. Resilience and observability

## 8.1 Failure modes and responses

Each row is a way the system can be hurt, how we notice, and what happens.

| # | Failure | Detection | Response | Operator sees |
|---|---|---|---|---|
| F1 | **Latency fault** (default 500 ms per request) | Request duration; world read time | Per-request timeout 3 s; the dashboard is served from the cache, so it never waits; snapshot ages by the read time | Normal screen, age near 1 to 2 s |
| F2 | **Error-rate fault** (503 with probability `rate`, default 0.25) | Transient errors in the client | Retry each failed request, budgeted (8.2). A refresh is 10 requests, so without retries almost every refresh fails (see 8.1.1) | Normal, or degraded if reads keep failing |
| F3 | **Stale-data fault** (header only) | `X-Simulator-Stale: true` | Mark the snapshot stale, mode `DEGRADED`, proposals provisional, execution needs an override | "Data flagged stale by the simulator" |
| F4 | **Simulator unavailable** (503 on all `/v1/*`, POST included) | Consecutive failures; write breaker | Keep the last good snapshot; back off 0.5 s doubling to 5 s; open the breaker; block execution | Banner, growing age, Approve disabled |
| F5 | **Stream rejected** (`stream_disconnect`, new connections only) | Connect gets 503 | Back off and retry; polling is unaffected because the stream is optional | Nothing, or a small "live tick feed off" indicator |
| F6 | **Stream silently stalled** (slow consumer dropped, connection open) | REST tick advances but no tick event; or no bytes for 30 s | Drop and reconnect; full REST refresh on reconnect | Nothing |
| F7 | **Malformed payload** (wrong type, unknown enum) | Validation fails | Reject the whole read; keep the last good snapshot; count and alert | Degraded with the reason |
| F8 | **Simulator restarts** | Connection errors, then recovery | As F4; on recovery a full read; state persists in the simulator, so the tick continues | Brief banner |
| F9 | **Simulator reset** | Tick goes backwards, or scenario or seed changes | New world epoch; drop caches; expire proposals (Figure 4.9) | "World was reset" notice |
| F10 | **Torn read** (tick moved during a read) | Tick read before and after | Retry up to 3 times; otherwise publish flagged `consistent: false` and refresh again next cycle | Small marker |
| F11 | **Overloading the simulator** | Request rate; connection resets | One poller; concurrency cap of 8 in flight (10 was tested safe); browser never calls the simulator | Nothing |
| F12 | **Our forecaster or optimizer fails or is slow** | Exception; solve time over 1 s | Fallback chain (8.3) | Proposal marked with the policy that made it |
| F13 | **SQLite unwritable** (locked, full, corrupt) | Write error | Keep serving reads; block execution (no write may happen without its ledger row); readiness reports degraded | "Ledger unavailable, execution disabled" |
| F14 | **POST outcome unknown** (timeout after the request left) | No response | Same key and body, bounded retries; then `UNKNOWN`, reconciled by key in the next snapshot | "Checking whether it was accepted" |
| F15 | **Approval arrives late** | Ticks between proposal and approval | Approval is an upper bound; re-validate; quantity can only shrink; expire after 40 ticks | Adjusted quantity shown |
| F16 | **Backend process restarts** | Process start | Reload open proposals and lines from SQLite; resume tracking; re-send nothing that has a key | Nothing |
| F17 | **Evaluator injects an event or fault mid-run** | Events list changes; fault symptoms | New events enter the forecast and the allocator on the next snapshot; faults follow F1 to F6 | Event shown with its explanation |
| F18 | **Startup order** (backend before simulator) | First reads fail | Retry with backoff until the simulator answers; `/readyz` stays 503 | "Waiting for simulator" |

### 8.1.1 Why retries are not optional (F2)

A world read is 10 requests (2 tick reads and 8 resources), and **any** failed request fails the read, because mixing pieces from different attempts would be a torn read. If each request fails independently with probability `p`:

| Retries per request | Probability a request still fails | Probability a refresh fails | Expected requests per request |
|---|---|---|---|
| 0 (no retries) | `p` | `1 - (1 - p)^10` | 1 |
| 2 (design) | `p^3` | `1 - (1 - p^3)^10` | `1 + p + p^2` |

At the simulator's default `p = 0.25` this gives:

| Retries | Refresh failure probability | Expected requests per request |
|---|---|---|
| 0 | 94% | 1.00 |
| 2 | 15% | 1.31 |

So at the default error-rate fault a client without retries would almost never refresh, and the dashboard would sit at the last snapshot. With two retries a refresh succeeds 85% of the time at a cost of 31% more requests, and the next cycle (0.5 s later) usually succeeds. The calculation assumes independent failures, which was measured: at the default rate, 431 of 1,800 requests failed (23.9%), two failures in a row occurred 6.2% of the time against 6.5% expected, and 97% of 10-request groups contained at least one failure against 94.7% predicted (`[live]`, findings section 5). Drill D2 repeats the measurement against the finished system.

Per-request retries are therefore the first resilience feature to build: without them the dashboard goes stale under the simulator's own default fault.

## 8.2 Retry, budget and breaker policy

| Item | Value | Reason |
|---|---|---|
| Per-request timeout | 3 s | Longer than the 0.5 s default latency fault, shorter than a human notices |
| Read retries | 2 retries (3 attempts), full jitter, base 100 ms, cap 1 s | F2 arithmetic above |
| Read retry budget | Retries at most 40% of requests over a rolling 30 s, with a floor of 3 retries so a quiet system can still retry | 31% is the expected need at `p = 0.25` (293 retries for 919 requests, 32%, measured live); the budget stops a retry storm if the simulator is really down |
| Write retries | Only on timeout or 5xx, same key and body, 3 attempts, base 200 ms, cap 2 s | Safe because of idempotency (part 7.4) |
| Never retried | 404, 409, 422 | The simulator is healthy and said no |
| Concurrency | At most 8 requests in flight to the simulator | Measured stable at 10; reset seen at 50 |
| World read deadline | 5 s | A read that long is too old to publish |
| Breakers | One each for reads, writes, stream connect; thresholds in Figure 5.4 | A failing write path must not stop reads |
| Poll backoff | `min(0.5 s x 2^failures, 5 s)` | Recovery is noticed within seconds even after a long outage |

## 8.3 Degradation ladders

Every capability has a simpler version behind it. Moving down is automatic; moving down is counted and visible; moving back up is automatic when the better version works again.

| Capability | First choice | Fallback 1 | Fallback 2 | Fallback 3 |
|---|---|---|---|---|
| Data | Live snapshot, age under 2 s | Last good snapshot, age shown, execution blocked | Explicit waiting state, nothing shown as fact | none |
| Forecast | Structural model with learned hour bands | Seasonal naive: same hour yesterday | Last observed demand | none |
| Allocation | Rolling-horizon LP | Cover-based greedy | Rules: fastest route for critical pairs | No recommendation, with the reason shown |
| Execution | Gate open: fresh data, ledger writable | Held: the operator can override, and the override is audited | Disabled: ledger unavailable | none |

*Table 8.1. Each ladder is independent: the allocator can be on `greedy` while the data is live. `fallback_activations_total{component, from, to}` counts every step down.*

## 8.4 Chaos drills

The drills run against the simulator's own fault injection and the bench simulator. A drill passes only if the criteria hold **and** the operator-visible state was correct throughout. Each drill is scripted (`bench/chaos.py`) so it can be repeated.

| Drill | Injection | Duration | Pass criteria |
|---|---|---|---|
| D1 Latency | `latency`, `delay_ms` 500 then 2000 | 60 s | Dashboard latency unchanged; snapshot age rises by about the read time; no errors surfaced |
| D2 Error rate | `error_rate`, `rate` 0.25 then 0.6 | 60 s | Measured refresh success matches 8.1.1 within a few points; breaker opens at 0.6 and closes after |
| D3 Stale | `stale_data` | 30 s | Mode `DEGRADED` within one cycle; values labelled; Approve needs override |
| D4 Unavailable | `unavailable` | 30 s | Last snapshot served; age grows; Approve blocked; recovery within 10 s of clearing (NFR-4) |
| D5 Stream | `stream_disconnect`, then restart the listener | 30 s | Nothing user-visible breaks; stream state shown |
| D6 Restart | `docker restart` the simulator | n/a | Recovery without manual action |
| D7 Reset | `POST /admin/reset` mid-run | n/a | Epoch increments; proposals expire; the grid shows the new world within 2 cycles |
| D8 Route closed | `route_disruption` on the route of an open proposal | n/a | Proposal superseded or its line skipped; no fuel lost to a `FAILED` allocation |
| D9 Spike | `demand_spike` x1.7 | 16 ticks | Forecast rises on the first snapshot; risk and proposals update; anomaly labelled explained |
| D10 Bad payload | A mock returning an unknown enum | n/a | Read rejected, last snapshot kept, counter increments |
| D11 Ledger | Make the SQLite file read-only | 30 s | Reads continue; execution disabled with a message |
| D12 POST timeout | Delay the POST past the timeout | n/a | Exactly one allocation exists afterwards; line resolved from `UNKNOWN` by key |

## 8.5 Observability

### Health endpoints and what they mean

The simulator's own `/v1/health` stays green during injected faults, so "alive" and "usable" are different things and are reported separately.

| Signal | Question it answers | Source |
|---|---|---|
| `/livez` | Is our process running? | Always 200 while the process runs. |
| `/readyz` | Do we have a recent snapshot? | 200 when a snapshot exists and is under 15 s old. |
| Simulator `/v1/health` | Is the simulator process alive? | Probed, shown as "alive" only |
| Mode (`LIVE`, `DEGRADED`, `STALE`) | Can the operator trust and act on the screen? | Derived from our own reads. |

### Metrics catalogue

Labels come only from fixed sets (endpoint names, outcome enums, station and fuel ids, policy names), so the number of series is bounded. The target is under 500 series, checked in a test.

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `fuelwatch_simulator_requests_total` | counter | `endpoint`, `outcome` (`ok`, `transient`, `rejected`, `invalid`, `circuit_open`) | Rate, error ratio, load on the simulator (NFR-3). One count per request, by its final outcome after retries; `circuit_open` means nothing was sent |
| `fuelwatch_simulator_request_seconds` | histogram | `endpoint` | Simulator latency |
| `fuelwatch_retries_total`, `fuelwatch_retry_budget_exhausted_total` | counter | `kind` (`read`, `write`) | Retry pressure |
| `fuelwatch_breaker_state` | gauge | `breaker` | 0 closed, 1 half-open, 2 open |
| `fuelwatch_snapshot_age_seconds`, `fuelwatch_snapshot_tick` | gauge | none | Freshness (NFR-1) |
| `fuelwatch_snapshot_inconsistent_total`, `fuelwatch_snapshot_stale_total` | counter | none | Torn reads and stale flags |
| `fuelwatch_mode` | gauge | `mode` | Which mode is active |
| `fuelwatch_world_resets_total` | counter | none | Resets detected |
| `fuelwatch_stream_state`, `fuelwatch_stream_stalls_total` | gauge, counter | `state` | Stream health |
| `fuelwatch_hours_to_stockout` | gauge | `station`, `fuel`, `quantile` | The risk picture, 24 series. 24 hours is reported when no stockout is projected within the 96-tick horizon |
| `fuelwatch_forecast_abs_pct_error` | histogram | `horizon` | Forecast quality against the noise floor. The only horizon scored is the one-step-ahead forecast made before each tick is learned, so `horizon` is `1` |
| `fuelwatch_forecast_interval_coverage` | gauge | none | Should sit near 0.8 |
| `fuelwatch_decision_seconds` | histogram | `policy` | Decision time |
| `fuelwatch_fallback_activations_total` | counter | `component`, `from`, `to` | Every step down a ladder |
| `fuelwatch_proposals_total` | counter | `status` | Proposal flow |
| `fuelwatch_allocation_outcomes_total` | counter | `outcome` | `arrived`, `failed`, `rejected_409`, `cancelled` |
| `fuelwatch_fuel_lost_litres_total` | counter | none | Litres sent minus litres received |
| `fuelwatch_http_request_seconds` | histogram | `route` (the route template), `method`, `status_class` | Our own API latency (NFR-2). `/metrics` and the endless `/api/stream` are not timed |
| `fuelwatch_ledger_errors_total` | counter | none | SQLite failures |

### Service level objectives

| SLO | Indicator | Objective |
|---|---|---|
| Dashboard availability | Share of `/api/snapshot` calls answered `200` | 99% over the run |
| Freshness | Share of time `snapshot_age_seconds` is at most 2 while the simulator is healthy | 95% |
| Execution correctness | Duplicate allocations; proposals rejected with a rule 409 on a fresh snapshot | 0 duplicates; under 1% rule rejections |
| Recovery | Time from fault clearing to `LIVE` | At most 10 s (NFR-4) |

Alerts, in order of importance: breaker open for over 30 s; mode `STALE`; a fallback active for over 1 minute; stream stalled; forecast coverage outside 0.7 to 0.9; ledger errors above zero; an unexplained anomaly open. A burn-rate pair on the availability SLO (fast and slow window) is the standard form and is a stretch item.

### Structured logs

JSON lines with `ts`, `level`, `event`, `tick`, `epoch`, `request_id` and event-specific fields. Every API response carries an `X-Request-ID` header (a caller's own is kept), and the same id is on the log lines written while it was handled; background work has none. Lines that are not one of the events below have the event `log` and a `msg`. Event names are a fixed list so they can be searched and alerted on:

`snapshot.published`, `snapshot.read_failed`, `snapshot.inconsistent`, `breaker.opened`, `breaker.closed`, `world.reset`, `forecast.fallback`, `allocator.fallback`, `proposal.created`, `proposal.superseded`, `proposal.expired`, `execution.attempt`, `execution.outcome`, `execution.unknown_resolved`, `stream.stalled`, `fault.suspected`.

### Dashboards (Grafana)

A health board (mode, breakers, snapshot age); simulator calls (rate, error ratio, latency, retries); decisions (fallbacks, decision time); outcomes (proposals, allocations, fuel lost); forecast quality. Grafana is a stretch item: the metrics endpoint comes first, because it is what the SLOs and drills read.

## 8.6 Test strategy

| Level | What |
|---|---|
| Unit | Models, error mapping, client, poller, API with a faked transport |
| Contract | The client against recorded real payloads, so a simulator change shows up |
| Property | For random worlds, every proposal satisfies all rules in 6.4 |
| Layering | Only `client.py` imports `httpx`; intelligence imports no I/O |
| Integration | Backend against a paused simulator in CI |
| Chaos | Drills D1 to D12 |
| Replay | Policies on identical replays, with a determinism hash |
| Load | k6 open model (`constant-arrival-rate`, then `ramping-arrival-rate` to find the knee), aimed at our API only, repeated under a latency fault |
