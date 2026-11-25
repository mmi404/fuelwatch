"""Typed models for every simulator resource.

Shapes were taken from the live simulator (see docs/SIMULATOR_STATUS_FINDINGS.md).
Models are frozen and strict about enums and numeric types, so a malformed
payload fails loudly instead of leaking a bad value into the intelligence layer.
Unknown extra fields are ignored so a harmless additive change does not break us.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FuelType(StrEnum):
    DIESEL = "DIESEL"
    PETROL = "PETROL"
    OCTANE = "OCTANE"


class DepotStatus(StrEnum):
    OPEN = "OPEN"
    CONSTRAINED = "CONSTRAINED"  # cosmetic in the simulator: blocks nothing


class StationStatus(StrEnum):
    OPEN = "OPEN"
    OUTAGE = "OUTAGE"  # served demand is zero and POSTs return STATION_CLOSED


class RouteStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DISRUPTED = "DISRUPTED"


class AllocationStatus(StrEnum):
    PENDING = "PENDING"
    IN_TRANSIT = "IN_TRANSIT"
    ARRIVED = "ARRIVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EventType(StrEnum):
    DEMAND_SPIKE = "demand_spike"
    SHIPMENT_DELAY = "shipment_delay"
    ROUTE_DISRUPTION = "route_disruption"
    STATION_OUTAGE = "station_outage"
    DEPOT_CONSTRAINT = "depot_constraint"
    SUPPLY_SHORTFALL = "supply_shortfall"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=False)


# Litres per fuel type. Keys are validated against FuelType.
FuelMap = dict[FuelType, float]


class Instance(_Model):
    id: int
    scenario_id: str
    scenario_version: str
    seed: int
    sim_time: datetime
    tick: int = Field(ge=0)
    tick_minutes: int = Field(gt=0)
    status: str  # PAUSED or RUNNING


class Region(_Model):
    id: str
    name: str
    demand_factor: float = Field(gt=0)


class Depot(_Model):
    id: str
    name: str
    region_id: str
    status: DepotStatus
    dispatch_capacity_per_tick: float = Field(ge=0)
    capacity: FuelMap
    inventory: FuelMap


class Station(_Model):
    id: str
    name: str
    region_id: str
    status: StationStatus
    demand_profile: str
    demand_multiplier: float = Field(ge=0)
    capacity: FuelMap
    inventory: FuelMap


class Route(_Model):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int = Field(ge=0)
    max_shipment: float = Field(gt=0)
    status: RouteStatus


class SupplyArrival(_Model):
    id: str
    depot_id: str
    fuel_type: FuelType
    quantity: float = Field(ge=0)
    planned_tick: int = Field(ge=0)
    actual_tick: int | None = None
    status: str  # SCHEDULED, DELAYED, ARRIVED


class Event(_Model):
    id: int
    type: EventType
    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=0)
    status: str  # SCHEDULED, ACTIVE, RESOLVED
    parameters: dict[str, Any] = Field(default_factory=dict)


class Allocation(_Model):
    id: int
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: FuelType
    quantity: float = Field(gt=0)
    created_tick: int = Field(ge=0)
    departure_tick: int | None = None
    expected_arrival_tick: int | None = None
    actual_arrival_tick: int | None = None
    status: AllocationStatus
    failure_reason: str | None = None


class Metrics(_Model):
    served_demand_liters: float
    unmet_demand_liters: float
    service_level: float
    allocation_liters: float
    allocation_failures: int


class DemandRecord(_Model):
    """One row of /v1/demand-history: a station, fuel and tick."""

    id: int
    station_id: str
    fuel_type: FuelType
    tick: int = Field(ge=0)
    sim_time: datetime
    demand_liters: float
    served_liters: float
    unmet_liters: float


class AllocationRequest(_Model):
    """Body for POST /v1/allocations."""

    idempotency_key: str = Field(min_length=1, max_length=150)
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: FuelType
    quantity: float = Field(gt=0)
