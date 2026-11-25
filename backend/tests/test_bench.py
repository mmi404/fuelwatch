import argparse
import asyncio

import httpx

from fuelwatch.bench import SCENARIOS, RunResult, _p95, main_async, markdown_table, run_one
from fuelwatch.client import SimulatorClient

from .conftest import Sleeps
from .test_executor import ExecSim


def run(coro):
    return asyncio.run(coro)


def client_for(sim: ExecSim) -> SimulatorClient:
    return SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())


def test_the_none_policy_posts_nothing_and_greedy_posts_what_the_live_allocator_would():
    async def scenario():
        none_sim, greedy_sim = ExecSim(), ExecSim()
        a = await run_one(client_for(none_sim), "baseline", "none", horizon=3)
        b = await run_one(client_for(greedy_sim), "baseline", "greedy", horizon=3)
        return a, b, none_sim, greedy_sim

    none, greedy, none_sim, greedy_sim = run(scenario())
    assert none.shipments_posted == 0 and none_sim.posts == []
    assert greedy.shipments_posted >= 1 and all(p["idempotency_key"].startswith("bench-baseline-greedy-") for p in greedy_sim.posts)
    assert greedy.litres_shipped == sum(p["quantity"] for p in greedy_sim.posts)
    assert none_sim.admin_calls[:3] == ["/admin/faults/clear", "/admin/pause", "/admin/reset"]  # a clean, paused, reset world first


def test_scenario_events_are_replayed_through_admin_events():
    sim = ExecSim()
    run(run_one(client_for(sim), "final_combined", "none", horizon=1))
    assert sim.admin_calls.count("/admin/events") == len(SCENARIOS["final_combined"]) == 3


def test_a_rejected_post_is_counted_by_code_not_raised():
    async def scenario():
        sim = ExecSim()
        sim.post_mode, sim.reject_code = "reject", "ROUTE_DISRUPTED"
        return await run_one(client_for(sim), "baseline", "greedy", horizon=2)

    result = run(scenario())
    assert result.shipments_posted == 0 and result.posts_rejected.get("ROUTE_DISRUPTED", 0) >= 1


def test_the_runner_refuses_to_reset_the_primary_simulator():
    args = argparse.Namespace(sim="http://localhost:8000", allow_primary=False)
    assert run(main_async(args)) == 2


def test_table_and_percentile():
    r = RunResult("baseline", "none", 192, 0.5, 100.0, 200.0, 0, 0, 0.0, {}, 0.0, 12.5, 1.0, 2.0, "abc", 1.0)
    assert markdown_table([r]).splitlines()[2].startswith("| baseline | none | 0.500 | 100 |")
    assert _p95([1.0, 2.0, 3.0, 4.0, 100.0]) == 100.0 and _p95([]) == 0.0
