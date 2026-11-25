import pytest
from pydantic import ValidationError

from fuelwatch.models import (
    Allocation,
    AllocationRequest,
    AllocationStatus,
    Depot,
    DepotStatus,
    Event,
    EventType,
    FuelType,
    Instance,
    Station,
)

from .conftest import INSTANCE, WORLD


def test_every_world_resource_parses():
    assert Instance.model_validate(INSTANCE).tick_minutes == 15
    depot = Depot.model_validate(WORLD["/v1/depots"][0])
    assert depot.status is DepotStatus.OPEN
    assert depot.inventory[FuelType.DIESEL] == 60000
    assert Station.model_validate(WORLD["/v1/stations"][0]).capacity[FuelType.OCTANE] == 9000
    event = Event.model_validate(WORLD["/v1/events"][0])
    assert event.type is EventType.ROUTE_DISRUPTION
    assert event.parameters["route_ids"] == ["route-gazipur-mirpur"]


def test_allocation_lifecycle_statuses_parse():
    for item in WORLD["/v1/allocations"]:
        Allocation.model_validate(item)
    assert {s.value for s in AllocationStatus} == {"PENDING", "IN_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"}


def test_unknown_fuel_type_is_rejected():
    bad = {**WORLD["/v1/depots"][0], "inventory": {"KEROSENE": 1.0}}
    with pytest.raises(ValidationError):
        Depot.model_validate(bad)


def test_unknown_status_is_rejected():
    bad = {**WORLD["/v1/stations"][0], "status": "EXPLODED"}
    with pytest.raises(ValidationError):
        Station.model_validate(bad)


def test_wrong_type_is_rejected():
    bad = {**INSTANCE, "tick": "soon"}
    with pytest.raises(ValidationError):
        Instance.model_validate(bad)


def test_negative_tick_minutes_rejected():
    with pytest.raises(ValidationError):
        Instance.model_validate({**INSTANCE, "tick_minutes": 0})


def test_extra_fields_are_ignored():
    Instance.model_validate({**INSTANCE, "new_field_added_later": True})


def test_models_are_frozen():
    inst = Instance.model_validate(INSTANCE)
    with pytest.raises(ValidationError):
        inst.tick = 5  # type: ignore[misc]


def test_allocation_request_validation():
    ok = AllocationRequest(
        idempotency_key="p1-0",
        source_depot_id="d",
        destination_station_id="s",
        route_id="r",
        fuel_type=FuelType.DIESEL,
        quantity=100,
    )
    assert ok.model_dump(mode="json")["fuel_type"] == "DIESEL"
    with pytest.raises(ValidationError):
        AllocationRequest(**{**ok.model_dump(), "quantity": 0})
    with pytest.raises(ValidationError):
        AllocationRequest(**{**ok.model_dump(), "idempotency_key": ""})
    with pytest.raises(ValidationError):
        AllocationRequest(**{**ok.model_dump(), "idempotency_key": "x" * 151})
