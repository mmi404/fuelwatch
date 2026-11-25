import asyncio

import httpx
import pytest

from fuelwatch.client import SimulatorClient, raise_for_status
from fuelwatch.errors import (
    MalformedResponse,
    SimulatorInvalidRequest,
    SimulatorNotFound,
    SimulatorRejected,
    SimulatorUnavailable,
)
from fuelwatch.models import AllocationRequest, FuelType

from .conftest import FakeClock, Sleeps, make_world, transport_for


def run(coro):
    return asyncio.run(coro)


def response(status: int, body, headers=None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


# -- error shapes -----------------------------------------------------------------


def test_409_detail_code_is_parsed_and_not_transient():
    with pytest.raises(SimulatorRejected) as err:
        raise_for_status(
            response(409, {"detail": {"code": "ROUTE_CAPACITY_EXCEEDED", "message": "too big"}})
        )
    assert err.value.code == "ROUTE_CAPACITY_EXCEEDED"
    assert err.value.transient is False


def test_503_error_code_is_parsed_and_transient():
    with pytest.raises(SimulatorUnavailable) as err:
        raise_for_status(response(503, {"error": {"code": "FAULT_INJECTED", "message": "boom"}}))
    assert err.value.code == "FAULT_INJECTED"
    assert err.value.transient is True


def test_503_stream_shape_uses_detail_code():
    with pytest.raises(SimulatorUnavailable) as err:
        raise_for_status(response(503, {"detail": {"code": "FAULT_INJECTED"}}))
    assert err.value.code == "FAULT_INJECTED"


def test_422_and_404_are_not_transient():
    with pytest.raises(SimulatorInvalidRequest) as e422:
        raise_for_status(response(422, {"detail": [{"loc": ["body", "quantity"], "msg": "bad"}]}))
    assert e422.value.transient is False
    with pytest.raises(SimulatorNotFound) as e404:
        raise_for_status(response(404, {"detail": {"code": "ALLOCATION_NOT_FOUND", "message": "x"}}))
    assert e404.value.code == "ALLOCATION_NOT_FOUND"


def test_non_json_error_body_does_not_crash():
    with pytest.raises(SimulatorUnavailable):
        raise_for_status(httpx.Response(502, text="<html>bad gateway</html>"))


# -- client behaviour ------------------------------------------------------------


def test_network_error_becomes_transient_unavailable():
    def boom(request):
        raise httpx.ConnectError("refused")

    client = SimulatorClient("http://sim", transport=httpx.MockTransport(boom), sleep=Sleeps())
    with pytest.raises(SimulatorUnavailable) as err:
        run(client.instance())
    assert err.value.transient is True


def test_timeout_becomes_transient_unavailable():
    def slow(request):
        raise httpx.ReadTimeout("slow")

    client = SimulatorClient("http://sim", transport=httpx.MockTransport(slow), sleep=Sleeps())
    with pytest.raises(SimulatorUnavailable):
        run(client.instance())


def test_malformed_payload_raises_malformed_response():
    world = make_world()
    world["/v1/instance"]["tick"] = "not-a-number"
    client = SimulatorClient("http://sim", transport=transport_for(world))
    with pytest.raises(MalformedResponse):
        run(client.instance())


def test_create_allocation_sends_expected_body_and_parses_result():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        seen["method"] = request.method
        return response(201, make_world()["/v1/allocations"][0])

    client = SimulatorClient("http://sim", transport=httpx.MockTransport(handler))
    result = run(
        client.create_allocation(
            AllocationRequest(
                idempotency_key="k1",
                source_depot_id="depot-gazipur",
                destination_station_id="station-mirpur",
                route_id="route-gazipur-mirpur",
                fuel_type=FuelType.DIESEL,
                quantity=1000,
            )
        )
    )
    assert seen["method"] == "POST"
    assert b'"idempotency_key":"k1"' in seen["body"].replace(b" ", b"")
    assert result.id == 1


def test_create_allocation_409_surfaces_the_code():
    def handler(request):
        return response(409, {"detail": {"code": "INSUFFICIENT_INVENTORY", "message": "no stock"}})

    client = SimulatorClient("http://sim", transport=httpx.MockTransport(handler))
    req = AllocationRequest(
        idempotency_key="k9",
        source_depot_id="d",
        destination_station_id="s",
        route_id="r",
        fuel_type=FuelType.PETROL,
        quantity=5,
    )
    with pytest.raises(SimulatorRejected) as err:
        run(client.create_allocation(req))
    assert err.value.code == "INSUFFICIENT_INVENTORY"


# -- reading the world -----------------------------------------------------------


def test_read_world_builds_consistent_snapshot():
    client = SimulatorClient("http://sim", transport=transport_for(make_world(tick=7)))
    snap = run(client.read_world())
    assert snap.tick == 7
    assert snap.tick_minutes == 15
    assert snap.consistent is True and snap.stale is False
    assert len(snap.stations) == 1 and len(snap.allocations) == 2
    # PENDING counts as on the road, ARRIVED does not.
    assert [a.id for a in snap.in_transit] == [1]


def test_stale_header_is_surfaced():
    client = SimulatorClient(
        "http://sim", transport=transport_for(make_world(), headers={"X-Simulator-Stale": "true"})
    )
    assert run(client.read_world()).stale is True


def test_torn_read_is_retried_then_succeeds():
    base = make_world()
    ticks = iter([1, 2, 2, 2])  # first attempt: before=1 after=2 (torn); second: 2 and 2

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/instance":
            return response(200, {**base["/v1/instance"], "tick": next(ticks)})
        return response(200, base[path])

    client = SimulatorClient("http://sim", transport=httpx.MockTransport(handler), torn_read_attempts=3)
    snap = run(client.read_world())
    assert snap.consistent is True
    assert snap.tick == 2


def test_persistent_torn_read_is_flagged_not_fatal():
    base = make_world()
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/instance":
            counter["n"] += 1
            return response(200, {**base["/v1/instance"], "tick": counter["n"]})
        return response(200, base[path])

    client = SimulatorClient("http://sim", transport=httpx.MockTransport(handler), torn_read_attempts=2)
    snap = run(client.read_world())
    assert snap.consistent is False
