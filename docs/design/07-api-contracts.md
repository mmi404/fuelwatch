# 7. API contracts

Two interfaces matter: the one FuelWatch **exposes** to its dashboard and to operators, and the one it **consumes** from the simulator. Both are specified here, with their error handling.

## 7.1 FuelWatch API

### Conventions

| Topic | Rule |
|---|---|
| Format | JSON over HTTP. Times are ISO 8601; litres are numbers; ticks are integers. |
| Freshness | Every response about the world carries `meta.tick` and `meta.age_seconds`. |
| Caching | `GET /api/snapshot` is served from memory. An `ETag` of `epoch-tick` allows `304` responses. |
| Errors | `{"error": {"code": "...", "message": "...", "details": {...}}}` with a stable `code`. |
| Authentication | None on read endpoints. Write and control endpoints require the header `X-Operator-Token`: the shared secret `OPERATOR_TOKEN`, or, when `ACCOUNTS` lists people (`name:role:key`), that person's own key, which switches the shared secret off. A wrong or missing header is `401 UNAUTHORIZED`; with neither configured every such endpoint answers `503 OPERATOR_TOKEN_NOT_SET`, so the system fails closed. Roles: `operator` approves and rejects; `supervisor` can also approve with `override_stale`, change the autopilot and use the simulator controls, and an operator who tries is answered `403 SUPERVISOR_REQUIRED`. The shared secret has the supervisor role. |
| Idempotency | Approve and reject are idempotent: repeating them returns the current state, never a second action. |
| Browser rule | The dashboard calls only this API. It never calls the simulator. |

### Endpoint catalogue

| Method | Path | Purpose | Returns | Auth |
|---|---|---|---|---|
| GET | `/livez` | The process is up | `200 {"status":"ok"}` | no |
| GET | `/readyz` | A recent snapshot exists | `200 {"ready":true, ...meta}` or `503 {"ready":false,"reason":...}` | no |
| GET | `/api/health/components` | Health of FuelWatch itself, read when asked: backend API, database (a real SQLite read), fuel simulator, forecast, decision engine, tick stream and execution gate, each `HEALTHY`, `DEGRADED`, `DOWN` or `UNKNOWN` with a plain detail, and an `overall` state. `http` has our p95 latency (estimated from histogram buckets) and 5xx share over `window`: `recent` (about 5 minutes or more, measured between calls) or `since_start`; both are null without requests. `process` has resident memory (null without `/proc`) and CPU. Like other optional reads it can be refused under load | `200 {overall, checked_at, components, http, process}` | no |
| GET | `/api/snapshot` | The whole current world | `200 {"meta", "world"}`, or `503` with code `NO_SNAPSHOT` before the first read | no |
| GET | `/api/status` | Mode, tick and age, epoch and resets, breaker states, retry budget, history and forecast health, tick-stream state, execution counters and whether the gate is open, autopilot settings, proposals by status, open alerts, versions | `200` | no |
| GET | `/api/risks` | One row per station and fuel: hours to stockout P50 and P90, risk, level, ordered by urgency | `200 [..]` | no |
| GET | `/api/forecast` | Demand forecast with P10, P50, P90, filterable by station and fuel | `200 [..]` | no |
| GET | `/api/anomalies` | Demand anomalies open now, unexplained first: station, kind (`explained` or `unexplained`), direction, deviation, z, first and last flagged tick, summary | `200 [..]` | no |
| GET | `/api/delivery-checks` | Late supply deliveries and fuel lost on arrival open now, overdue first: id, kind (`late_delivery` or `lost_fuel`), state (`overdue`, `arrived_late` or `lost`), place id and name, fuel, litres, ticks late (null for a loss), first and last tick, summary | `200 [..]` | no |
| GET | `/api/me` | Whose key this is: `{name, role}`. The shared token answers `operator`, `supervisor` | `200`, `401`, `503` | yes |
| GET | `/api/proposals` | Proposals of the current world, newest first, with lines and explanation cards. Query: `status` (comma list), `limit` 1 to 200 | `200 [..]` or `422` | no |
| GET | `/api/proposals/{id}` | One proposal with lines and attempts | `200` or `404` | no |
| GET | `/api/alerts` | Stockout alerts of the current world, newest first. Query: `state` (comma list of open, acknowledged, cleared) | `200 [..]` | no |
| POST | `/api/proposals/{id}/approve` | Approve; execution follows the gate. Body may carry `override_stale`. Returns the proposal with each line's outcome, including `HELD` with the reason when the gate is closed | `200`, `401`, `404`, `409 INVALID_STATE` (not open), `503 OPERATOR_TOKEN_NOT_SET` | yes |
| POST | `/api/proposals/{id}/reject` | Reject an open (`PROPOSED` or `HELD`) proposal | `200`, `401`, `404`, `409` | yes |
| GET | `/api/allocations` | Tracked shipments from our ledger (lines sent, being sent, unknown, rejected or failed), newest first, each with the simulator's own allocation record when it is in the snapshot. Query: `status` (comma list), `limit` 1 to 500 | `200 [..]` or `422` | no |
| GET | `/api/audit` | Audit trail, newest first. Query: `limit` 1 to 500, `offset` | `200 [..]` or `422` | no |
| GET | `/api/autopilot` | The autopilot switch and its guard limits | `200` | no |
| PUT | `/api/autopilot` | Change the switch and any guard limits sent (`enabled`, `min_confidence`, `max_liters_per_proposal`, `max_liters_per_tick`, `max_proposals_per_tick`); fields not sent keep their value | `200`, `401`, `422` | yes |
| POST | `/api/sim/run`, `/api/sim/pause`, `/api/sim/step`, `/api/sim/reset` | Demo controls, proxied to the simulator's admin endpoints so the browser never calls it. One attempt each, since they are not idempotent; each is audited and wakes the poller | `200 {ok, action, simulator}`, `401`, `404` (unknown control), `503` or `502` when the simulator fails | yes |
| GET | `/api/stream` | Browser server-sent events: `snapshot` (a new world was read), `proposals` (created, superseded or expired counts), `proposal` (one proposal changed state), `autopilot`. A comment line starts the stream, the current snapshot tick follows, and a keepalive comment is sent every 15 s. A slow browser loses its oldest events; the REST endpoints are the truth | `text/event-stream` | no |
| GET | `/metrics` | Prometheus exposition | text | no |

### Response shape of `/api/snapshot`

```
{
  "meta": {
    "has_snapshot": true,
    "tick": 85,
    "tick_minutes": 15,
    "age_seconds": 0.42,
    "simulator_stale_header": false,   // the simulator sent X-Simulator-Stale: true
    "consistent_read": true,           // the tick did not move during the read
    "consecutive_failures": 0,
    "last_error": null,
    "degraded": false                  // serving the last good snapshot while reads fail
  },
  "world": {
    "instance": {...}, "regions": [...], "depots": [...], "stations": [...],
    "routes": [...], "supply_arrivals": [...], "events": [...], "allocations": [...],
    "metrics": {...}, "fetched_at": "...", "stale": false, "consistent": true
  }
}
```

## 7.2 Simulator endpoints we consume

The table is generated from the simulator's OpenAPI document so it cannot drift from the real API. The last two columns are our annotations; an endpoint without one shows as "Not used".

<!-- BEGIN GENERATED: simulator-endpoints -->
*Generated from the simulator's own OpenAPI document (30 operations). Do not edit by hand.*

| Method | Path | Parameters | Used by | Notes |
|---|---|---|---|---|
| GET | `/v1/allocations` | none | Poller, executor | Source of in-transit fuel and of reconciliation by idempotency key. |
| POST | `/v1/allocations` | body `AllocationCreate` | Executor, bench | The only write we make. Idempotent by key; a validation 409 does not consume the key. |
| POST | `/v1/allocations/{allocation_id}/cancel` | `allocation_id` (path, integer, required) | Executor | Only valid while `PENDING`; refunds the depot. |
| GET | `/v1/demand-history` | `station_id` (query, string); `limit` (query, integer) | History sync, bench | Newest first, default limit 200, one response capped at 2,000 rows (166 ticks), `station_id` filtered reads are separate. Forecaster training data. |
| GET | `/v1/depots` | none | Poller | Stock, capacity, dispatch capacity, status. |
| GET | `/v1/depots/{entity_id}` | `entity_id` (path, string, required) | Not used | The list already holds the same data. |
| GET | `/v1/events` | none | Poller | Early warning: scheduled and active events, including future ones. |
| GET | `/v1/health` | none | Probe | Alive only: stays green during injected faults, so it cannot tell us the simulator is usable. |
| GET | `/v1/instance` | none | Poller, bench | Tick, `tick_minutes`, status, scenario, seed. Read twice per world read (torn-read guard). |
| GET | `/v1/metrics` | none | Poller, bench | Service level, unmet litres, allocation failures. |
| GET | `/v1/regions` | none | Poller | Static. |
| GET | `/v1/routes` | none | Poller | Transit ticks, maximum shipment, status. |
| GET | `/v1/stations` | none | Poller | Stock, capacity, status, `demand_multiplier` (shows an active spike). |
| GET | `/v1/stations/{entity_id}` | `entity_id` (path, string, required) | Not used | The list already holds the same data. |
| GET | `/v1/stream` | none | Stream listener | Tick hint only. Rejected with 503 when the stream fault is on; slow clients are dropped silently. |
| GET | `/v1/supply-arrivals` | none | Poller | Planned tick moves when delayed; quantity changes on a shortfall. |
| GET | `/` | none | Not used |  |
| GET | `/admin` | none | Not used | The evaluator's admin page. |
| GET | `/admin/audit` | `limit` (query, integer) | Not used | Useful for debugging a benchmark. |
| GET | `/admin/events` | none | Not used | `/v1/events` is the same data. |
| POST | `/admin/events` | body `EventCreate` | Bench | Replays scenario events. Body: type, start_tick, duration_ticks, parameters. |
| GET | `/admin/faults` | none | Chaos drills | Active faults. |
| POST | `/admin/faults` | body `FaultCreate` | Chaos drills | Body: type, duration_seconds (max 3600), parameters. |
| POST | `/admin/faults/clear` | none | Chaos drills |  |
| POST | `/admin/pause` | none | Demo control, bench |  |
| POST | `/admin/reset` | none | Bench, demo control | Returns to tick 0 and tick length 15 minutes. |
| POST | `/admin/run` | none | Demo control, bench | Runs at the configured speed. |
| GET | `/admin/stats` | none | Not used | Row counts and scenario file. |
| POST | `/admin/step` | none | Bench, demo control | Advances exactly one tick; ignores parameters. |
| POST | `/admin/toggle` | none | Not used | Run and pause are explicit instead. |
<!-- END GENERATED: simulator-endpoints -->

### Stream events

`GET /v1/stream` is server-sent events. We read it only as a hint.

| Event | Sent when | Use |
|---|---|---|
| `simulation.tick` | Every tick, with `tick` and `sim_time` | Triggers an early refresh and feeds the watchdog |
| `allocation.status_changed` | At POST or cancel only, not at departure, arrival or failure | Ignored; REST is read for the truth |
| `inventory.updated` | At POST or cancel, depot only | Ignored |
| `simulator.notice` | On reset and on a tick error | Logged; a reset notice triggers a refresh and a world-reset check |

## 7.3 How simulator errors are handled

Both error shapes are normalised in the client into typed errors. The last two columns are the behaviour of the layers above the client.

| Simulator response | Client exception | Transient | Proposal line | What the operator sees |
|---|---|---|---|---|
| Network error, timeout | `SimulatorUnavailable` | yes | `SENDING` retried, then `UNKNOWN` | "Could not confirm, checking" |
| `503 error.code FAULT_INJECTED` | `SimulatorUnavailable` | yes | as above | Degraded banner |
| `404` | `SimulatorNotFound` | no | `REJECTED` | "Not found" |
| `422` | `SimulatorInvalidRequest` | no | `REJECTED`; also counted as a bug | "Our request was malformed" |
| `409 ROUTE_DISRUPTED` | `SimulatorRejected` | no | `REJECTED`; the next proposal avoids the route | "Route closed" |
| `409 STATION_CLOSED` or `DEPOT_CLOSED` | `SimulatorRejected` | no | `REJECTED` | "Station in outage" |
| `409 INSUFFICIENT_INVENTORY` | `SimulatorRejected` | no | `REJECTED`; re-planned | "Depot stock changed" |
| `409 DISPATCH_CAPACITY_EXCEEDED` | `SimulatorRejected` | no | `REJECTED`; retried in a later tick | "Depot dispatch full this tick" |
| `409 ROUTE_CAPACITY_EXCEEDED` or `DESTINATION_CAPACITY_EXCEEDED` | `SimulatorRejected` | no | `REJECTED`; counted as a bug in the allocator | "Quantity above limit" |
| `409 ROUTE_MISMATCH` | `SimulatorRejected` | no | `REJECTED`; counted as a bug | "Invalid route" |
| `409 IDEMPOTENCY_KEY_MISMATCH` | `SimulatorRejected` | no | `REJECTED`; counted as a bug | "Duplicate key conflict" |
| `2xx` with a body that fails validation | `MalformedResponse` | no | n/a | Degraded, last good snapshot kept, alert raised |

A 409 means the simulator is healthy and said no, so it never counts as a failure for the breaker and is never retried. The three allocator bugs in the table (`ROUTE_CAPACITY_EXCEEDED`, `ROUTE_MISMATCH`, `IDEMPOTENCY_KEY_MISMATCH`) should never happen on a fresh snapshot; the benchmark asserts that their count is zero.

## 7.4 Idempotency

| Property | Behaviour | Evidence |
|---|---|---|
| Same key, same body | `201` with the existing allocation | `[live]` |
| Same key, different body | `409 IDEMPOTENCY_KEY_MISMATCH`; the key stays bound to the first body | `[live]` |
| Validation 409 | The key is **not** consumed | `[live]` |
| Key length | 1 to 150 characters | `[live]` schema |

Our keys are `fw-p{proposal_id}-l{line_id}`. The key and the body are stored before the request is sent, and a retry always sends the stored pair. That gives exactly-once execution across timeouts and across a crash between "stored" and "sent".
