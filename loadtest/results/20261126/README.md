# Load test results, 2026-11-26

k6 0.54.0 (`grafana/k6` image) against the FuelWatch backend, on one Windows 11 laptop under Docker Desktop (16 logical cores). The simulator, backend, Prometheus, Grafana and k6 all ran on that same machine, so the absolute numbers are indicative, not a capacity statement. The backend was a single uvicorn process at simulator tick 70 with the autopilot on, `ALLOCATOR=lp`.

| File | Scenario |
|---|---|
| `reads.txt` | `constant-arrival-rate`, 50 iterations per second for 60 s; each iteration reads `/api/status`, `/api/risks`, `/api/proposals?status=PROPOSED` and `/api/alerts` (200 requests per second) |
| `plan-endpoints.txt` | the same rate on `/api/proposals`, `/api/allocations`, `/api/forecast` (150 requests per second) |
| `reads-with-1s-simulator-latency-fault.txt` | the `reads` scenario while the simulator had a 1,000 ms latency fault active |
| `ramp-to-find-the-knee.txt` | `ramping-arrival-rate` from 20 to 1,000 iterations per second in four 30 s stages |
| `ramp-with-load-shedding.txt` | the same ramp after load shedding was added, run later on a paused simulator; see the notes at the top of the file |

Command: `docker compose --profile loadtest run --rm -e SCENARIO=reads -e RATE=50 -e DURATION=60s k6` (see `loadtest/dashboard.js`).
