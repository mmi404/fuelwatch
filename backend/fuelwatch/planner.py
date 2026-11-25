"""The allocation policy chain (design 6.4, fallback chain).

    lp  --(infeasible, over 1 s, exception)-->  greedy  --(exception)-->  rules  --(exception)-->  no plan

Each step down is counted and logged through the observer, and the plan says which policy produced it
and which one failed first, so the proposal and its confidence can show it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .allocate import Plan, plan_rules, plan_shipments
from .forecast import DemandForecaster
from .lp import LpFailed, plan_lp
from .metrics import NOOP
from .snapshot import WorldSnapshot
from .stockout import Assessment

log = logging.getLogger("fuelwatch.planner")

Planner = Callable[[WorldSnapshot, DemandForecaster, list[Assessment]], Plan]
CHAINS: dict[str, tuple[str, ...]] = {"lp": ("lp", "greedy", "rules"), "greedy": ("greedy", "rules"), "rules": ("rules",)}
STEPS: dict[str, Planner] = {"lp": plan_lp, "greedy": plan_shipments, "rules": plan_rules}


class PlanningFailed(Exception):
    """Every policy in the chain failed: there is no recommendation this tick."""


def plan_with_fallback(snapshot: WorldSnapshot, forecaster: DemandForecaster, assessments: list[Assessment], policy: str = "lp", obs=NOOP, steps: dict[str, Planner] | None = None) -> tuple[Plan, float]:
    """Plan with `policy`, stepping down the chain on failure. Returns the plan and the seconds the winning policy took."""
    chain = CHAINS.get(policy)
    if chain is None:
        raise ValueError(f"unknown allocator policy {policy!r}; choose from {', '.join(CHAINS)}")
    table = steps or STEPS
    first_failed: str | None = None
    errors: list[str] = []
    for i, name in enumerate(chain):
        started = time.perf_counter()
        try:
            plan = table[name](snapshot, forecaster, assessments)
        except Exception as exc:  # noqa: BLE001 - any failure steps down the chain
            errors.append(f"{name}: {exc!r}")
            log.warning("allocator %s failed: %r", name, exc, exc_info=not isinstance(exc, LpFailed))
            first_failed = first_failed or name
            if i + 1 < len(chain):
                obs.fallback("allocator", name, chain[i + 1])
            continue
        seconds = time.perf_counter() - started
        if first_failed:
            from dataclasses import replace

            plan = replace(plan, fallback_from=first_failed)
        return plan, seconds
    raise PlanningFailed("; ".join(errors))
