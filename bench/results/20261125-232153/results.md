# Replay benchmark 20261125-232153

Commit 4f4ea9b, horizon 192 ticks, simulator seed 12345. Raw output in `runs/`.

| Scenario | Policy | Service level | Unmet L | Shipments | Litres shipped | Rejected POSTs | Failed allocations | Lost at arrival L | Stockout pair-hours | Policy p95 ms | Fallbacks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | greedy | 1.000 | 0 | 128 | 113,848 | 0 | 0 | 0 | 0 | 3.49 | 0 |
| baseline | lp | 1.000 | 0 | 130 | 115,120 | 0 | 0 | 0 | 0 | 182.02 | 0 |
| demand_spike | greedy | 1.000 | 0 | 130 | 115,758 | 0 | 0 | 0 | 0 | 3.45 | 0 |
| demand_spike | lp | 1.000 | 0 | 133 | 118,171 | 0 | 0 | 0 | 0 | 181.89 | 0 |
| supply_disruption | greedy | 1.000 | 0 | 128 | 113,848 | 0 | 0 | 0 | 0 | 3.46 | 0 |
| supply_disruption | lp | 1.000 | 0 | 130 | 115,120 | 0 | 0 | 0 | 0 | 173.61 | 0 |
| final_combined | greedy | 1.000 | 0 | 130 | 116,043 | 0 | 0 | 0 | 0 | 3.6 | 0 |
| final_combined | lp | 1.000 | 0 | 134 | 118,915 | 0 | 0 | 0 | 0 | 173.86 | 0 |
