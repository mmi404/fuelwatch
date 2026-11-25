# Replay benchmark 20261125-230240

Commit 4f4ea9b, horizon 192 ticks, simulator seed 12345. Raw output in `runs/`.

| Scenario | Policy | Service level | Unmet L | Shipments | Litres shipped | Rejected POSTs | Failed allocations | Lost at arrival L | Stockout pair-hours | Policy p95 ms | Fallbacks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| stress_crunch | none | 0.243 | 267,302 | 0 | 0 | 0 | 0 | 0 | 461 | 0.03 | 0 |
| stress_crunch | rules | 0.985 | 5,200 | 51 | 281,372 | 0 | 0 | 0 | 15.25 | 1.48 | 0 |
| stress_crunch | greedy | 0.989 | 3,982 | 263 | 274,349 | 0 | 0 | 0 | 11 | 4.77 | 0 |
| stress_crunch | lp | 0.995 | 1,856 | 264 | 274,851 | 0 | 0 | 0 | 6.25 | 228.47 | 0 |
| stress_roads | none | 0.363 | 150,393 | 0 | 0 | 0 | 0 | 0 | 421 | 0.03 | 0 |
| stress_roads | rules | 1.000 | 0 | 34 | 211,855 | 0 | 0 | 0 | 0 | 0.42 | 0 |
| stress_roads | greedy | 1.000 | 0 | 181 | 163,821 | 0 | 0 | 0 | 0 | 4.11 | 0 |
| stress_roads | lp | 1.000 | 0 | 183 | 165,031 | 0 | 0 | 0 | 0 | 187.45 | 0 |
| stress_outage_rebound | none | 0.362 | 151,624 | 0 | 0 | 0 | 0 | 0 | 415.25 | 0.03 | 0 |
| stress_outage_rebound | rules | 0.972 | 6,608 | 29 | 188,392 | 0 | 0 | 0 | 19.5 | 0.42 | 0 |
| stress_outage_rebound | greedy | 0.972 | 6,608 | 174 | 157,276 | 0 | 0 | 0 | 19.5 | 3.87 | 0 |
| stress_outage_rebound | lp | 0.972 | 6,608 | 174 | 158,983 | 0 | 0 | 0 | 19.5 | 193.64 | 0 |
| stress_everything | none | 0.303 | 197,535 | 0 | 0 | 0 | 0 | 0 | 452.5 | 0.03 | 0 |
| stress_everything | rules | 0.969 | 8,709 | 40 | 229,771 | 0 | 0 | 49 | 12.5 | 0.48 | 0 |
| stress_everything | greedy | 0.970 | 8,379 | 206 | 201,049 | 0 | 0 | 0 | 10.25 | 4.05 | 0 |
| stress_everything | lp | 0.981 | 5,374 | 210 | 206,125 | 0 | 0 | 0 | 6.75 | 211.07 | 0 |
