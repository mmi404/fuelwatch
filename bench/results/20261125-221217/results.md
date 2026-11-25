# Replay benchmark 20261125-221217

Commit 3f54ab0, horizon 192 ticks, simulator seed 12345. Raw output in `runs/`.

| Scenario | Policy | Service level | Unmet L | Shipments | Litres shipped | Rejected POSTs | Failed allocations | Lost at arrival L | Stockout pair-hours | Decision p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | none | 0.461 | 100,452 | 0 | 0 | 0 | 0 | 0 | 291.5 | 8.54 |
| baseline | greedy | 1.000 | 0 | 128 | 113,848 | 0 | 0 | 0 | 0 | 9.58 |
| demand_spike | none | 0.454 | 103,333 | 0 | 0 | 0 | 0 | 0 | 299.25 | 9.81 |
| demand_spike | greedy | 1.000 | 0 | 130 | 115,758 | 0 | 0 | 0 | 0 | 10.15 |
| supply_disruption | none | 0.461 | 100,452 | 0 | 0 | 0 | 0 | 0 | 291.5 | 7.83 |
| supply_disruption | greedy | 1.000 | 0 | 128 | 113,848 | 0 | 0 | 0 | 0 | 9.22 |
| final_combined | none | 0.453 | 103,916 | 0 | 0 | 0 | 0 | 0 | 300.25 | 10.19 |
| final_combined | greedy | 1.000 | 0 | 130 | 116,043 | 0 | 0 | 0 | 0 | 14.68 |
