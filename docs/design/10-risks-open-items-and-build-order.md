# 10. Risks, open items and build order

## 10.1 Where the project stands

How much of this design exists is stated in the project README, which is the one place for implementation status. This document is the specification. Every target and number below is a requirement or a calculation until it is measured.

## 10.2 Non-functional targets: how each will be verified

| Target | State |
|---|---|
| NFR-1 Snapshot age at most 2 s | Met in a 90 s run at about one tick a second with the autopilot on: at most 1.06 s |
| NFR-2 `/api/snapshot` p95 under 100 ms | Met at light load: p95 33 ms over 1,324 requests in the same run. Not measured under the k6 load, where other read endpoints reached a p95 of 170 ms at 200 requests/s |
| NFR-3 At most 30 requests per second to the simulator | Met in the same run: 14.5 a second on average, 17.9 in the busiest 20 s |
| NFR-4 Recovery within 10 s | Measured on one laptop: live again 2.7 to 3.7 s after clearing outages of 16, 40 and 55 s (up to 23.7 s before the breaker's cool-down was capped at 5 s) |
| NFR-5 Zero duplicates and avoidable losses | No duplicate and no failed shipment among the 18 of that run, and no rejected send in the replay benchmark; the forced-timeout drill (D12) was not run |
| NFR-6 Honest degraded state | Checked by the drill checklist |
| NFR-7 Start in under 5 minutes | Measured on one laptop with the base images already downloaded, so not a true first run: building both images with no cache took 221 s, and a fresh clone from GitHub with the quick-start command took 331 s while other work ran on the laptop, which is over the target. A first download of the base images was not timed |
| NFR-8 Reproducible benchmark | The simulator's determinism is verified (identical state hashes across two runs); the benchmark itself is not run |
| NFR-9 Bounded series count | 379 series after that run, 6 more than a minute earlier as new outcomes first appeared; a long run was not done. A unit test checks that labels come from fixed sets |

## 10.3 Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Evaluators run the simulator at other speed or tick length | Medium | High | Read `tick_minutes` at runtime; test both settings (ADR-08) |
| Admin endpoints are unavailable or restricted in the evaluators' setup | Low | Medium | The product path uses only `/v1/*`; admin is for demo controls and the benchmark. If it is missing, the controls are hidden and the system still works |
| Approval is slower than the simulation | High | Medium | Approval as upper bound; re-validation; optional pause control (ADR-07) |
| Fuel lost to a route closed after POST | Medium | Medium | Block routes covered by scheduled disruptions; show `FAILED` honestly (part 6.4 rule 4) |
| An evaluator resets the simulator mid-demo | Medium | Medium | Reset detection and world epochs (ADR-09) |
| Retries are missing when the error fault is demonstrated | Medium | High | Build per-request retries first (build order, item 1) |
| LP no better than greedy | Low | Medium | Report the truth; keep the better policy as default |
| Cold start: early proposals made on a rough forecast | High | Low | Cover-based target tolerates it; confidence shown as Low until a day of data |
| The simulator's behaviour differs from what was read in its source | Low | Medium | The demand model was validated on 1,572 rows; contract tests will catch changes |
| Too much scope for the time | High | High | The cut line below |

## 10.4 Open questions

| Question | Why it matters | How it closes |
|---|---|---|
| Which speed and tick length the evaluators run | Pacing of approvals and of the demo | Cannot be known; both settings are tested |
| Why a connection reset occurred at 50 concurrent clients | Only matters if the simulator were load-tested directly, which the design avoids | Reproduce from a Linux client; low priority |
| Whether evaluators will inject events that start in the future or at the current tick | Decides how much warning the event feed gives | Handle both: scheduled events are read early; current-tick events show up through `demand_multiplier` and route status |
| Real refresh request rate | Confirms the load budget | Request counter during a run |
| LP solve time at 48 ticks | The 1 s timeout assumes it is small | Measure when built |

## 10.5 Build order

Dependencies, not dates. Each item is done when its acceptance check passes.

| Order | Item | Needs | Acceptance |
|---|---|---|---|
| 1 | Simulator client, models, snapshot and poller, with per-request retries, a concurrency cap and the health `mode` | none | Unit tests; drills D2 and D4 on the live simulator |
| 2 | Demand history sync, world epochs, reset detection | 1 | History matches `/v1/demand-history`; drill D7 |
| 3 | Forecaster and stockout estimator | 2 | Forecast error near the noise floor; an alert for Tongi diesel well before tick 65 |
| 4 | Rules, greedy allocator, proposals and explanations | 3 | Property test: every proposal satisfies all rules |
| 5 | Executor, ledger, approve endpoint | 4 | Approved proposal reaches `ARRIVED`; no duplicate after a forced timeout (D12) |
| 6 | Dashboard: grid, risk inbox, proposal card, health banner | 3, 4, 5 | The demo script below |
| 7 | Replay benchmark with `none` and `greedy` | 4 | Greedy beats 0.46; result file with determinism hash |
| 8 | Compose stack and `.env`, then README numbers | 6, 7 | Clean clone to running in one command |
| 9 | Metrics, then chaos drills as scripts | 1 | SLO queries answer from real data |
| 10 | LP allocator and its benchmark | 7 | LP versus greedy versus none, reported as measured |
| 11 | k6 load test, CI | 8, 9 | Raw output committed |
| 12 | Stream listener, Grafana, burn-rate alerts, challenger models | 9 | Stretch |

**Cut line.** Items 1 to 8 are the product: the four things the brief asks for, plus proof. Everything from 9 down is added value and is dropped from the bottom if time runs out. The README is edited to match what runs at every step.

## 10.6 Demo script

| Step | Shows | Requirement |
|---|---|---|
| Open the dashboard on a freshly reset simulator | Grid of 4 stations by 3 fuels, depots, routes, ages on every value | FR-1 |
| Run the simulation | Risk inbox fills; Tongi diesel rises to the top | FR-2 |
| Open a proposal | Explanation card: signals, binding constraints, alternatives, confidence | FR-3 |
| Approve it | Line goes to `SUBMITTED`, then `IN_TRANSIT`, then `ARRIVED`; audit row | FR-4 |
| Inject `final_combined` events | Forecast and risk move; a closed route is avoided | FR-2, FR-3 |
| Inject `unavailable` | Banner, growing age, Approve blocked; clear it and watch recovery | FR-5 |
| Show the benchmark table | Do nothing versus greedy versus LP, with the raw file | FR-6 |
| Show `/metrics` or the Grafana panel | Retries, breaker, fallbacks | FR-7 |
