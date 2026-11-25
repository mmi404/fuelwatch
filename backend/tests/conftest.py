"""Shared fixtures: payloads shaped exactly like the live simulator's responses."""

from __future__ import annotations

import copy
from typing import Any, Callable

import httpx
import pytest

INSTANCE = {
    "id": 1,
    "scenario_id": "baseline",
    "scenario_version": "1.0",
    "seed": 12345,
    "sim_time": "2026-01-01T00:00:00",
    "tick": 0,
    "tick_minutes": 15,
    "status": "PAUSED",
}

WORLD: dict[str, Any] = {
    "/v1/regions": [{"id": "region-dhaka", "name": "Dhaka Division", "demand_factor": 1.0}],
    "/v1/depots": [
        {
            "id": "depot-gazipur",
            "name": "Gazipur Depot",
            "region_id": "region-dhaka",
            "status": "OPEN",
            "dispatch_capacity_per_tick": 12000.0,
            "capacity": {"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
            "inventory": {"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
        }
    ],
    "/v1/stations": [
        {
            "id": "station-mirpur",
            "name": "Mirpur Fuel Station",
            "region_id": "region-dhaka",
            "status": "OPEN",
            "demand_profile": "urban_high",
            "demand_multiplier": 1.0,
            "capacity": {"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000},
            "inventory": {"DIESEL": 9000, "PETROL": 9000, "OCTANE": 5000},
        }
    ],
    "/v1/routes": [
        {
            "id": "route-gazipur-mirpur",
            "source_depot_id": "depot-gazipur",
            "destination_station_id": "station-mirpur",
            "transit_ticks": 2,
            "max_shipment": 7000.0,
            "status": "AVAILABLE",
        }
    ],
    "/v1/supply-arrivals": [
        {
            "id": "supply-001",
            "depot_id": "depot-gazipur",
            "fuel_type": "DIESEL",
            "quantity": 18000.0,
            "planned_tick": 12,
            "actual_tick": None,
            "status": "SCHEDULED",
        }
    ],
    "/v1/events": [
        {
            "id": 1,
            "type": "route_disruption",
            "start_tick": 5,
            "end_tick": 8,
            "status": "SCHEDULED",
            "parameters": {"route_ids": ["route-gazipur-mirpur"]},
        }
    ],
    "/v1/allocations": [
        {
            "id": 1,
            "idempotency_key": "k1",
            "source_depot_id": "depot-gazipur",
            "destination_station_id": "station-mirpur",
            "route_id": "route-gazipur-mirpur",
            "fuel_type": "DIESEL",
            "quantity": 1000.0,
            "created_tick": 0,
            "departure_tick": None,
            "expected_arrival_tick": None,
            "actual_arrival_tick": None,
            "status": "PENDING",
            "failure_reason": None,
        },
        {
            "id": 2,
            "idempotency_key": "k2",
            "source_depot_id": "depot-gazipur",
            "destination_station_id": "station-mirpur",
            "route_id": "route-gazipur-mirpur",
            "fuel_type": "PETROL",
            "quantity": 500.0,
            "created_tick": 0,
            "departure_tick": 1,
            "expected_arrival_tick": 3,
            "actual_arrival_tick": 3,
            "status": "ARRIVED",
            "failure_reason": None,
        },
    ],
    "/v1/metrics": {
        "served_demand_liters": 0.0,
        "unmet_demand_liters": 0.0,
        "service_level": 1.0,
        "allocation_liters": 0.0,
        "allocation_failures": 0,
    },
}


def make_world(tick: int = 0) -> dict[str, Any]:
    world = copy.deepcopy(WORLD)
    world["/v1/instance"] = {**INSTANCE, "tick": tick}
    return world


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def world() -> Callable[..., dict[str, Any]]:
    return make_world


def transport_for(world: dict[str, Any], *, headers: dict[str, str] | None = None) -> httpx.MockTransport:
    """A transport that serves a static world."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = world.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json=body, headers=headers or {})

    return httpx.MockTransport(handler)
