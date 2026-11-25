# FuelWatch engineering design

This is the design of FuelWatch: what it must do, how it is structured, how data and control flow through it, how it fails and recovers, and why each choice was made over its alternatives. It is written to be read by an engineer who has not seen the code, and to be checked against the code.

## How to read it

This is the design of the whole system. Sentences about behaviour describe what the system will do; the project README states how much of it exists. Facts about the simulator carry a tag saying how they were established: `[code]` read in the simulator's source, `[live]` confirmed against a running simulator, `[inferred]` follows from the code but was not exercised. The evidence is in [../SIMULATOR_STATUS_FINDINGS.md](../SIMULATOR_STATUS_FINDINGS.md).

Time is counted in **ticks** everywhere inside the system. Hours are derived from `tick_minutes`, which is read from the simulator at runtime and never assumed (the guide and the image disagree about the default).

## Contents

| Part | What it answers |
|---|---|
| [01 Requirements and constraints](01-requirements-and-constraints.md) | What must the system do, how well, and what does the simulator force on the design? |
| [02 Architecture](02-architecture.md) | Context, containers, backend components and layering rules, deployment. |
| [03 Data model](03-data-model.md) | The simulator's domain model, the in-memory snapshot, the persistence schema. |
| [04 Flows](04-flows.md) | Sequence and activity diagrams: ingest, recommend, approve and execute, failure, replay benchmark. |
| [05 State machines](05-state-machines.md) | Proposal, allocation, circuit breaker, system mode and event lifecycles. |
| [06 Algorithms](06-algorithms.md) | Demand model, forecaster, stockout estimate, allocation (greedy and LP), anomaly detection, evaluation. |
| [07 API contracts](07-api-contracts.md) | FuelWatch API, the simulator endpoints we consume, error mapping, idempotency. |
| [08 Resilience and observability](08-resilience-and-observability.md) | Failure modes and responses, retry and breaker policy, metrics, SLOs, logs, tests. |
| [09 Decision log](09-decision-log.md) | Each significant decision with the options considered and the evidence, and what changed from the first plan. |
| [10 Risks, open items and build order](10-risks-open-items-and-build-order.md) | What is risky, what is still unknown, the order to build in, and the demo script. |

A single PDF of all parts is built from these files: `FuelWatch-Engineering-Design.pdf`.

## The design in eight rules

Each rule comes from something we observed in the simulator, not from a preference.

| # | Rule | Why (evidence) |
|---|---|---|
| 1 | One poller reads the simulator; everything else reads an immutable snapshot. | The simulator is a small single process: 60 to 76 requests per second, and a connection reset at 50 concurrent clients. |
| 2 | REST is the truth; the event stream is only a hint, and the system works without it. | The stream is silent about departures, arrivals, failures and station stock, and drops slow clients without closing the connection. |
| 3 | Never assume time. Read `tick_minutes`; detect a tick that goes backwards as a world reset. | The image and the guide disagree on tick length; `POST /admin/reset` restores 15 minutes; state survives a container restart. |
| 4 | Every number shown carries its tick and its age. | Data can be stale (a header says so) and the snapshot can age during an outage. |
| 5 | Recommendations obey the simulator's own validation rules, in the simulator's own order. | Fuel is lost silently if headroom is ignored; a route disrupted after posting fails and is not refunded. |
| 6 | Approval authorises an upper bound; execution re-validates against fresh state. | At about 4.6 ticks per second a 30 second approval is about 140 ticks. |
| 7 | Every write is idempotent and its key is stored before it is sent. | A request that times out may still have succeeded. |
| 8 | Every intelligent component has a simpler fallback, and using it is counted and visible. | Any model can fail; the platform must still give a safe answer. |

## Format

The diagrams are Mermaid blocks inside the Markdown, which GitHub renders directly. The PDF is the same content with the diagrams drawn, built from these files with a Mermaid renderer and headless Chrome.
