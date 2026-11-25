"""Replay benchmark (design 4.5 and 6.8).

The simulator is deterministic, so the same crisis can be given to every policy. For each scenario and
policy the runner resets a dedicated simulator, replays the scenario's events, then for every tick reads
the world through the same client the live system uses, asks the policy for shipments, posts them, and
steps. The policy code is imported from the live system, not re-implemented. Results are written with
their raw output so a README number can cite a file.

    python -m fuelwatch.bench --sim http://localhost:8001 --out ../bench/results

It resets the simulator it is pointed at: use a dedicated instance, never the one a demo is running on.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .allocate import Plan, plan_shipments
from .client import SimulatorClient
from .config import Settings
from .errors import SimulatorError, SimulatorRejected
from .forecast import DemandForecaster
from .models import AllocationRequest, AllocationStatus
from .snapshot import WorldSnapshot
from .stockout import assess

HORIZON = 192

# Events of the simulator's own scenario files (`preloaded_events`), replayed through POST /admin/events
# because the scenario cannot be switched at runtime (docs/SIMULATOR_STATUS_FINDINGS.md).
SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "baseline": [],
    "demand_spike": [
        {"type": "demand_spike", "start_tick": 8, "duration_ticks": 12, "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}},
    ],
    "supply_disruption": [
        {"type": "shipment_delay", "start_tick": 6, "duration_ticks": 1, "parameters": {"depot_ids": ["depot-gazipur"], "fuel_types": ["DIESEL"], "delay_ticks": 8}},
        {"type": "supply_shortfall", "start_tick": 10, "duration_ticks": 1, "parameters": {"depot_ids": ["depot-patiya"], "factor": 0.6}},
    ],
    "final_combined": [
        {"type": "demand_spike", "start_tick": 8, "duration_ticks": 16, "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.7}},
        {"type": "shipment_delay", "start_tick": 10, "duration_ticks": 1, "parameters": {"depot_ids": ["depot-gazipur"], "fuel_types": ["DIESEL", "PETROL"], "delay_ticks": 8}},
        {"type": "route_disruption", "start_tick": 14, "duration_ticks": 10, "parameters": {"route_ids": ["route-gazipur-mirpur"]}},
    ],
}

Policy = Callable[[WorldSnapshot, DemandForecaster, list], Plan]


def policy_none(snapshot: WorldSnapshot, forecaster: DemandForecaster, assessments: list) -> Plan:
    return Plan(snapshot.tick, (), ())


def policy_greedy(snapshot: WorldSnapshot, forecaster: DemandForecaster, assessments: list) -> Plan:
    return plan_shipments(snapshot, forecaster, assessments)


POLICIES: dict[str, Policy] = {"none": policy_none, "greedy": policy_greedy}


@dataclass
class RunResult:
    scenario: str
    policy: str
    ticks: int
    service_level: float
    unmet_liters: float
    served_liters: float
    simulator_allocation_failures: int
    shipments_posted: int
    litres_shipped: float
    posts_rejected: dict[str, int]
    litres_lost_at_arrival: float
    stockout_pair_hours: float
    decision_ms_mean: float
    decision_ms_p95: float
    history_hash: str
    wall_seconds: float
    allocations: list[dict[str, Any]] = field(default_factory=list)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


async def _prepare(client: SimulatorClient, events: list[dict[str, Any]]) -> None:
    await client.admin_command("faults/clear")
    await client.admin_command("pause")
    await client.admin_command("reset")
    for e in events:
        await client.admin_command("events", e)
    instance = await client.instance()
    if instance.tick != 0:
        raise RuntimeError(f"simulator did not reset to tick 0 (tick {instance.tick})")


async def _train(client: SimulatorClient, forecaster: DemandForecaster, snap: WorldSnapshot, rows_per_tick: int) -> None:
    """Teach the forecaster the newest complete ticks, the way the live system reads history."""
    if snap.tick == 0:
        return
    records = await client.demand_history(limit=rows_per_tick * 3)
    by_tick: Counter[int] = Counter(r.tick for r in records)
    rows = [(r.station_id, r.fuel_type.value, r.tick, r.demand_liters) for r in records if by_tick[r.tick] >= rows_per_tick and r.tick < snap.tick]
    forecaster.train(rows, snap.stations, snap.regions, snap.events, snap.tick_minutes)


async def run_one(client: SimulatorClient, scenario: str, policy_name: str, horizon: int = HORIZON, log: Callable[[str], None] = lambda _: None) -> RunResult:
    policy = POLICIES[policy_name]
    started = time.monotonic()
    await _prepare(client, SCENARIOS[scenario])
    forecaster = DemandForecaster()
    first: WorldSnapshot | None = None
    snap: WorldSnapshot | None = None
    decision_ms: list[float] = []
    rejected: Counter[str] = Counter()
    posted = 0
    shipped = 0.0

    for tick in range(horizon):
        snap = await client.read_world()
        if first is None:
            first = snap
        rows_per_tick = len(snap.stations) * 3
        await _train(client, forecaster, snap, rows_per_tick)
        t0 = time.perf_counter()
        assessments = assess(snap, forecaster)
        plan = policy(snap, forecaster, assessments)
        decision_ms.append((time.perf_counter() - t0) * 1000)
        for i, s in enumerate(plan.shipments):
            request = AllocationRequest(idempotency_key=f"bench-{scenario}-{policy_name}-{tick}-{i}", source_depot_id=s.depot_id, destination_station_id=s.station_id, route_id=s.route_id, fuel_type=s.fuel_type, quantity=float(s.quantity))
            try:
                await client.create_allocation(request)
                posted += 1
                shipped += s.quantity
            except SimulatorRejected as exc:
                rejected[exc.code or "REJECTED"] += 1
            except SimulatorError as exc:
                rejected[type(exc).__name__] += 1
        await client.admin_command("step")
        if tick % 48 == 47:
            log(f"  {scenario}/{policy_name}: tick {tick + 1}/{horizon}")

    assert first is not None
    final = await client.read_world()
    history = []
    for station in final.stations:
        history.extend(await client.demand_history(station_id=station.id, limit=2000))
    history.sort(key=lambda r: (r.tick, r.station_id, r.fuel_type.value))
    digest = hashlib.sha256("\n".join(f"{r.station_id},{r.fuel_type.value},{r.tick},{r.demand_liters:.3f},{r.served_liters:.3f},{r.unmet_liters:.3f}" for r in history).encode()).hexdigest()

    # Litres lost at arrival, by conservation of stock: what arrived minus what the tank actually gained.
    served: Counter[tuple[str, str]] = Counter()
    for r in history:
        served[(r.station_id, r.fuel_type.value)] += r.served_liters
    arrived: Counter[tuple[str, str]] = Counter()
    for a in final.allocations:
        if a.status is AllocationStatus.ARRIVED:
            arrived[(a.destination_station_id, a.fuel_type.value)] += a.quantity
    start = {s.id: s for s in first.stations}
    lost = 0.0
    for st in final.stations:
        for fuel in st.capacity:
            key = (st.id, fuel.value)
            gained = float(st.inventory.get(fuel, 0.0)) - float(start[st.id].inventory.get(fuel, 0.0)) + served[key]
            lost += max(0.0, arrived[key] - gained)
    stockout_ticks = len({(r.station_id, r.fuel_type.value, r.tick) for r in history if r.unmet_liters > 1e-6})
    metrics = final.metrics
    return RunResult(
        scenario=scenario,
        policy=policy_name,
        ticks=horizon,
        service_level=round(metrics.service_level, 4),
        unmet_liters=round(metrics.unmet_demand_liters, 1),
        served_liters=round(metrics.served_demand_liters, 1),
        simulator_allocation_failures=metrics.allocation_failures,
        shipments_posted=posted,
        litres_shipped=round(shipped, 1),
        posts_rejected=dict(rejected),
        litres_lost_at_arrival=round(lost, 1),
        stockout_pair_hours=round(stockout_ticks * final.tick_minutes / 60.0, 2),
        decision_ms_mean=round(statistics.fmean(decision_ms), 2),
        decision_ms_p95=round(_p95(decision_ms), 2),
        history_hash=digest,
        wall_seconds=round(time.monotonic() - started, 1),
        allocations=[a.model_dump(mode="json") for a in final.allocations],
    )


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def markdown_table(results: list[RunResult]) -> str:
    head = "| Scenario | Policy | Service level | Unmet L | Shipments | Litres shipped | Rejected POSTs | Failed allocations | Lost at arrival L | Stockout pair-hours | Decision p95 ms |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
    rows = [
        f"| {r.scenario} | {r.policy} | {r.service_level:.3f} | {r.unmet_liters:,.0f} | {r.shipments_posted} | {r.litres_shipped:,.0f} | {sum(r.posts_rejected.values())} | {r.simulator_allocation_failures} | {r.litres_lost_at_arrival:,.0f} | {r.stockout_pair_hours:g} | {r.decision_ms_p95:g} |"
        for r in results
    ]
    return head + "\n".join(rows) + "\n"


async def main_async(args: argparse.Namespace) -> int:
    if args.sim.rstrip("/").endswith(":8000") and not args.allow_primary:
        print("refusing to reset the simulator on :8000 (the demo instance); start a second one on :8001 or pass --allow-primary", file=sys.stderr)
        return 2
    client = SimulatorClient(args.sim, settings=Settings(request_timeout=10.0))
    scenarios = list(SCENARIOS) if args.scenarios == "all" else args.scenarios.split(",")
    policies = args.policies.split(",")
    for name in scenarios:
        if name not in SCENARIOS:
            raise SystemExit(f"unknown scenario {name}; choose from {', '.join(SCENARIOS)}")
    for name in policies:
        if name not in POLICIES:
            raise SystemExit(f"unknown policy {name}; choose from {', '.join(POLICIES)}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / stamp
    (out / "runs").mkdir(parents=True, exist_ok=True)
    results: list[RunResult] = []
    try:
        for scenario in scenarios:
            for policy in policies:
                print(f"running {scenario} / {policy}", flush=True)
                result = await run_one(client, scenario, policy, args.horizon, log=lambda m: print(m, flush=True))
                results.append(result)
                (out / "runs" / f"{scenario}-{policy}.json").write_text(json.dumps(asdict(result), indent=1), encoding="utf-8")
                print(f"  service level {result.service_level:.3f}, unmet {result.unmet_liters:,.0f} L, {result.shipments_posted} shipments", flush=True)
        reproducible: dict[str, bool] = {}
        for scenario, policy in [(args.repeat_scenario, "greedy")] if args.repeat else []:
            if scenario in scenarios and policy in policies:
                again = await run_one(client, scenario, policy, args.horizon)
                original = next(r for r in results if (r.scenario, r.policy) == (scenario, policy))
                reproducible[f"{scenario}/{policy}"] = again.history_hash == original.history_hash and again.service_level == original.service_level
                print(f"  re-run {scenario}/{policy}: {'identical' if reproducible[f'{scenario}/{policy}'] else 'DIFFERENT'}", flush=True)
    finally:
        with __import__("contextlib").suppress(Exception):
            await client.admin_command("pause")
            await client.admin_command("faults/clear")
            await client.admin_command("reset")
        await client.aclose()

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_commit(),
        "simulator": args.sim,
        "horizon_ticks": args.horizon,
        "reproducibility": reproducible,
        "results": [{k: v for k, v in asdict(r).items() if k != "allocations"} for r in results],
    }
    (out / "results.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out / "results.md").write_text(f"# Replay benchmark {stamp}\n\nCommit {summary['git_commit']}, horizon {args.horizon} ticks, simulator seed 12345. Raw output in `runs/`.\n\n{markdown_table(results)}", encoding="utf-8")
    print(f"\n{markdown_table(results)}\nwritten to {out}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay benchmark on a dedicated simulator")
    parser.add_argument("--sim", default="http://localhost:8001")
    parser.add_argument("--horizon", type=int, default=HORIZON)
    parser.add_argument("--scenarios", default="all")
    parser.add_argument("--policies", default="none,greedy")
    parser.add_argument("--out", default="bench/results")
    parser.add_argument("--repeat", action="store_true", help="re-run one scenario with greedy and compare the history hash")
    parser.add_argument("--repeat-scenario", default="final_combined")
    parser.add_argument("--allow-primary", action="store_true")
    sys.exit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
