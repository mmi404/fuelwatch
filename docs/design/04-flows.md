# 4. Flows

Sequence diagrams show who talks to whom and in what order; activity diagrams (drawn as flowcharts) show the decision logic inside one component.

## 4.1 Ingest cycle

The poller asks for the tick every `poll_interval` (0.5 s by default). It reads the whole world only when something warrants it. The tick is read before and after the world, because a read that straddles a tick boundary would mix two moments (a torn read).

```mermaid
sequenceDiagram
  autonumber
  participant P as Poller
  participant C as SimulatorClient
  participant S as Simulator
  participant T as SnapshotStore
  participant A as API
  loop every poll_interval
    P->>C: instance()
    C->>S: GET /v1/instance
    S-->>C: tick and tick_minutes
    alt tick moved, snapshot too old, refresh requested or last read inconsistent
      P->>C: read_world()
      C->>S: GET /v1/instance, before
      C->>S: eight resource reads in parallel
      C->>S: GET /v1/instance, after
      alt tick moved during the read
        C->>C: retry, up to 3 attempts, then mark consistent false
      end
      C-->>P: WorldSnapshot
      P->>T: publish
    else nothing changed
      P-->>P: skip the full read
    end
  end
  A->>T: current
  T-->>A: latest good snapshot, never partial
```

*Figure 4.1. A failed `instance()` or `read_world()` never reaches `publish`; the failure is recorded and the previous snapshot stays current.*

Load budget: a refresh is 10 requests (2 instance reads plus 8 resources), plus 1 instance request per cycle. At the default cadence the worst case is two refreshes per second, about 22 requests per second, which is the 30 requests per second budget (NFR-3) with a margin. The measured tick rate is about 4.6 per second, so the refresh rate is capped by the cadence, not by the tick rate. This is a calculation, to be confirmed by the request counter during the benchmark.

## 4.2 Recommendation cycle

Runs once per published snapshot, in the background, never in a request path.

```mermaid
sequenceDiagram
  autonumber
  participant T as Snapshot store
  participant F as Forecaster
  participant E as Stockout
  participant A as Allocator
  participant R as Proposals
  participant D as SQLite
  T->>F: new snapshot, tick N
  F->>D: reset check, upsert demand rows
  D-->>F: history to tick N
  F->>F: forecast, anomaly check
  F-->>E: demand quantiles
  E->>E: project each station and fuel
  E-->>A: risk and hours to stockout
  A->>A: rules, LP, greedy fallback
  A-->>R: candidate shipments
  R->>D: supersede changed, insert new
  R-->>T: proposals and alerts for the API
```

*Figure 4.2. Anything in this chain can fail; each step has a defined fallback (part 8.4), and a failure leaves the previous proposals in place, marked with their snapshot tick.*

## 4.3 Approve and execute

Approval is an upper bound. The operator approved a proposal made at tick N; execution happens at tick M, usually larger.

```mermaid
sequenceDiagram
  autonumber
  actor O as Operator
  participant UI as Dashboard
  participant B as Backend executor
  participant D as SQLite ledger
  participant S as Simulator
  O->>UI: approve proposal
  UI->>B: POST approve, token
  B->>B: gate: fresh snapshot, breaker closed, not expired
  alt gate closed
    B->>D: line HELD with reason
    B-->>UI: held, with reason and age
  else gate open
    B->>B: re-validate lines on the current snapshot
    B->>D: store key and quantity before sending
    B->>S: POST /v1/allocations
    alt 201
      S-->>B: allocation PENDING
      B->>D: line SUBMITTED
    else 409 business rule
      S-->>B: code such as ROUTE_DISRUPTED
      B->>D: line REJECTED, no retry
    else 503 or timeout
      B->>S: same key and body, bounded retries
      S-->>B: 201, existing record
      B->>D: line SUBMITTED
    end
    B->>B: request an immediate refresh
    B-->>UI: per-line outcome
  end
```

*Figure 4.3. A validation 409 does not consume the idempotency key, and the same key with the same body returns the existing allocation. Both facts are `[live]`, and both are why a retry is safe.*

Approval starts with a fresh read of the world, so the gate and the re-validation see the simulator as it is now, not as it was when the proposal was made. Lines are sent one after another and each one counts what the earlier ones used (depot stock, dispatch capacity, room at the station), because the snapshot cannot show them yet.

If all retries end with an unknown outcome, the line becomes `UNKNOWN` and every later snapshot is searched for an allocation carrying its key; a match resolves it to `SUBMITTED`. A snapshot read at least 10 seconds after the last attempt that still has no such allocation resolves it to `FAILED`, because by then the request cannot still be in flight. The system never guesses.

After submission the line is tracked from snapshots: `PENDING`, `IN_TRANSIT`, then `ARRIVED`, `FAILED` or `CANCELLED`, with `executed_quantity` compared to what the station actually received.

## 4.4 Failure and recovery

Shown for the hardest case: the simulator becomes unavailable (all `/v1/*` return 503, including POST), then recovers.

```mermaid
sequenceDiagram
  autonumber
  participant S as Simulator
  participant I as Ingest, client and poller
  participant T as Snapshot store
  participant A as Backend API
  participant UI as Dashboard
  S-->>I: 503 FAULT_INJECTED
  I->>T: record failure, last good snapshot kept
  I->>I: back off 0.5 s doubling to 5 s, breaker opens
  UI->>A: GET snapshot
  A-->>UI: last snapshot, degraded true, age growing
  UI->>UI: banner, values labelled with age, Approve disabled
  Note over S,I: fault clears
  I->>S: probe, half-open
  S-->>I: 200
  I->>S: full world read
  S-->>I: fresh data
  I->>T: publish, failures reset
  A-->>UI: degraded false, age near zero
```

*Figure 4.4. The last good snapshot keeps being served throughout, `degraded` and the failure count appear within seconds, and the feed catches up after the fault clears. The write path stays blocked until the breaker closes.*

## 4.5 Replay benchmark

The benchmark proves the intelligence. The simulator is deterministic (two runs gave identical state hashes), so every policy faces the same world.

```mermaid
sequenceDiagram
  autonumber
  participant B as Bench runner
  participant S as Bench simulator :8001
  participant PO as Policy
  participant M as Scorer
  loop for each policy: none, greedy, lp
    B->>S: POST /admin/reset
    B->>S: POST /admin/events, replay the scenario events
    loop for each tick until horizon
      B->>S: POST /admin/step
      B->>S: read the world through the same client
      B->>PO: decide(snapshot, history)
      PO-->>B: shipments or nothing
      B->>S: POST /v1/allocations, idempotent
    end
    B->>S: GET /v1/metrics
    S-->>M: service level, unmet litres, failures
    M->>M: stockout station hours from demand history
  end
  M-->>B: comparison table and raw output saved
```

*Figure 4.5. The policy code is exactly the code the live system runs, imported, not re-implemented. Results are saved with their raw output so the README table can cite a file.*

## 4.6 Poller refresh decision

```mermaid
flowchart TB
  start(["poll cycle"]) --> ti["read tick from the simulator"]
  ti -->|"error"| fail["record failure, keep snapshot, back off"]
  ti --> q1{"first read, refresh requested,<br/>or last read inconsistent?"}
  q1 -->|"yes"| full["read the whole world"]
  q1 -->|"no"| q2{"tick or tick length<br/>changed?"}
  q2 -->|"yes"| full
  q2 -->|"no"| q3{"snapshot older than<br/>refresh_max_age?"}
  q3 -->|"yes"| full
  q3 -->|"no"| skip["do nothing this cycle"]
  full -->|"error"| fail
  full --> pub["publish snapshot, reset failure count"]
  fail --> wait["sleep min of poll_interval x 2^failures, 5 s"]
  pub --> wait2["sleep poll_interval"]
  skip --> wait2
```

*Figure 4.6. Backoff is exponential and capped at 5 seconds so recovery is detected quickly even after a long outage.*

## 4.7 Allocation decision

```mermaid
flowchart TB
  s(["snapshot and forecast ready"]) --> rank["rank station-fuel pairs by P90 hours to stockout"]
  rank --> risk{"any pair below<br/>the action threshold?"}
  risk -->|"no"| none["no proposal, record all clear"]
  risk -->|"yes"| rules["build rules: open routes, none disrupted<br/>within lead time, headroom net of stock on the road,<br/>depot stock minus reserve, dispatch capacity left"]
  rules --> lp["solve rolling-horizon LP, P90 demand"]
  lp -->|"timeout or infeasible"| gr["greedy allocator"]
  lp -->|"solved"| commit
  gr --> commit["keep only this tick's shipments"]
  commit --> sim{"same result as the open proposal<br/>for this station and fuel?"}
  sim -->|"yes"| keep["keep the open proposal"]
  sim -->|"no"| new["supersede it with a new proposal,<br/>attach explanation and alternatives"]
  keep --> out(["proposals for the API"])
  new --> out
```

*Figure 4.7. Only the first tick of the plan is committed, then the whole problem is solved again at the next snapshot (rolling horizon). That makes the plan self-correcting when events change the world.*

## 4.8 Execution gate

```mermaid
flowchart TB
  a(["approved proposal"]) --> g1{"snapshot fresh, not stale,<br/>write breaker closed?"}
  g1 -->|"no"| held["HELD: reason and data age shown"]
  g1 -->|"yes"| g3{"within expiry ticks and<br/>same world epoch?"}
  g3 -->|"no"| exp["EXPIRED: show the fresh proposal"]
  g3 -->|"yes"| rv["re-validate each line"]
  rv --> g4{"route disrupted, station closed,<br/>or depot stock gone?"}
  g4 -->|"yes"| skipline["skip line, record the reason"]
  g4 -->|"no"| q["quantity = min of approved, route max,<br/>headroom, depot stock, dispatch left"]
  q --> g5{"at least the minimum<br/>useful shipment?"}
  g5 -->|"no"| skipline
  g5 -->|"yes"| send["store key, then POST"]
```

*Figure 4.8. Quantity can only shrink at execution. Approving 5,000 L never results in 5,001.*

## 4.9 World reset detection

An evaluator can press Reset on the simulator at any moment. The tick then goes backwards, and everything we cached about the old world is wrong.

```mermaid
flowchart TB
  n(["new snapshot"]) --> c1{"tick lower than the last<br/>published tick?"}
  c1 -->|"yes"| reset
  c1 -->|"no"| c2{"scenario, seed, instance<br/>or tick length changed?"}
  c2 -->|"yes"| reset
  c2 -->|"no"| c3{"allocation count or served<br/>litres went backwards?"}
  c3 -->|"yes"| reset
  c3 -->|"no"| ok["same world, continue"]
  reset["new world epoch"] --> a1["drop forecasts and anomaly state"]
  a1 --> a2["expire open proposals as EXPIRED, reason world_reset"]
  a2 --> a3["reload demand history for the new epoch"]
  a3 --> a4["audit entry and counter increment"]
  a4 --> ok
```

*Figure 4.9. Without this, a reset during a demo would leave stale proposals on screen and a forecaster trained on the wrong world.*

A reset leaves the instance id, scenario and seed unchanged and restarts the history row ids (`[live]`), so only the tick and the running counters can reveal it. The check runs before the snapshot is published, so nobody sees the new world under the old epoch. One case is invisible: a reset followed by a run past the old tick within one poll interval, with nothing allocated. Demand is seeded, so the stored history is still correct for the new world, and the loss is limited to state we created ourselves; once the executor exists, an allocation of ours that is missing from the list is a further signal.
