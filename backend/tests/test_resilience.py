import asyncio

import httpx
import pytest

from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.errors import CircuitOpenError, SimulatorRejected, SimulatorUnavailable
from fuelwatch.health import Mode, compute_mode
from fuelwatch.models import AllocationRequest, FuelType
from fuelwatch.resilience import BreakerState, CircuitBreaker, RetryBudget

from .conftest import FakeClock, Sleeps, make_world, transport_for


def run(coro):
    return asyncio.run(coro)


def fault(status=503):
    return httpx.Response(status, json={"error": {"code": "FAULT_INJECTED", "message": "boom"}})


def request_body(key="fw-p1-l1"):
    return AllocationRequest(
        idempotency_key=key,
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type=FuelType.DIESEL,
        quantity=1000,
    )


def make_client(handler, *, settings=None, clock=None, sleeps=None):
    return SimulatorClient(
        "http://sim",
        settings=settings,
        transport=httpx.MockTransport(handler),
        clock=clock or FakeClock(),
        sleep=sleeps or Sleeps(),
    )


# -- retry budget --------------------------------------------------------------------


def test_budget_floor_allows_retries_on_a_quiet_system():
    clock = FakeClock()
    budget = RetryBudget(0.4, 30.0, floor=3, clock=clock)
    budget.record_request()
    assert [budget.try_acquire_retry() for _ in range(4)] == [True, True, True, False]


def test_budget_scales_with_request_volume_and_window_expires():
    clock = FakeClock()
    budget = RetryBudget(0.4, 30.0, floor=3, clock=clock)
    for _ in range(100):
        budget.record_request()
    allowed = sum(budget.try_acquire_retry() for _ in range(100))
    assert allowed == 40  # 40% of 100 requests
    clock.advance(31)
    budget.record_request()
    assert budget.try_acquire_retry() is True  # the old retries left the window


# -- circuit breaker -----------------------------------------------------------------


def test_breaker_opens_after_five_consecutive_failures_and_refuses():
    clock = FakeClock()
    b = CircuitBreaker("read", clock=clock)
    for _ in range(4):
        b.record_failure()
    assert b.state is BreakerState.CLOSED
    b.record_failure()
    assert b.state is BreakerState.OPEN and b.allow() is False


def test_breaker_opens_on_failure_rate_even_without_a_streak():
    b = CircuitBreaker("read", clock=FakeClock())
    for _ in range(5):  # alternate: never 5 in a row
        b.record_success()
        b.record_failure()
    assert b.state is BreakerState.OPEN  # 5 of 10 calls failed


def test_breaker_half_open_one_probe_two_successes_close_it():
    clock = FakeClock()
    b = CircuitBreaker("read", clock=clock)
    for _ in range(5):
        b.record_failure()
    clock.advance(4.9)
    assert b.allow() is False
    clock.advance(0.2)
    assert b.allow() is True and b.state is BreakerState.HALF_OPEN
    assert b.allow() is False  # only one probe at a time
    b.record_success()
    assert b.allow() is True  # second probe
    b.record_success()
    assert b.state is BreakerState.CLOSED


def test_failed_probe_reopens_with_doubled_cooldown_up_to_the_cap():
    clock = FakeClock()
    b = CircuitBreaker("read", clock=clock)
    for _ in range(5):
        b.record_failure()
    cooldowns = []
    for _ in range(5):
        clock.advance(b.info()["cooldown_seconds"] + 0.01)
        assert b.allow() is True
        b.record_failure()
        cooldowns.append(b.info()["cooldown_seconds"])
    assert cooldowns == [10.0, 20.0, 30.0, 30.0, 30.0]


def test_a_success_resets_the_streak():
    b = CircuitBreaker("read", clock=FakeClock())
    for _ in range(4):
        b.record_failure()
    b.record_success()
    b.record_failure()
    assert b.state is BreakerState.CLOSED


# -- retries in the client -------------------------------------------------------------


def test_read_fails_twice_then_succeeds_with_jittered_backoff():
    calls = []
    world = make_world()

    def handler(request):
        calls.append(request.url.path)
        return fault() if len(calls) <= 2 else httpx.Response(200, json=world["/v1/instance"])

    sleeps = Sleeps()
    client = make_client(handler, sleeps=sleeps)
    assert run(client.instance()).tick == 0
    assert len(calls) == 3 and client.retries_total == 2
    assert 0 <= sleeps.delays[0] <= 0.1 and 0 <= sleeps.delays[1] <= 0.2  # full jitter, capped exponential


def test_read_gives_up_after_two_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return fault()

    with pytest.raises(SimulatorUnavailable):
        run(make_client(handler).instance())
    assert len(calls) == 3  # first try plus two retries


@pytest.mark.parametrize("status, body", [(404, {"detail": {"code": "X", "message": "m"}}), (409, {"detail": {"code": "X", "message": "m"}}), (422, {"detail": [{"loc": ["b"], "msg": "bad"}]})])
def test_client_errors_are_never_retried(status, body):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json=body)

    client = make_client(handler)
    with pytest.raises(Exception) as err:
        run(client.instance())
    assert not isinstance(err.value, SimulatorUnavailable)
    assert len(calls) == 1
    assert client.breaker_states()["read"]["state"] == "CLOSED"  # the simulator answered, so it is healthy


def test_post_retries_resend_the_same_key_and_body():
    bodies = []
    world = make_world()

    def handler(request):
        bodies.append(request.read())
        if len(bodies) < 3:
            return fault()
        return httpx.Response(201, json=world["/v1/allocations"][0])

    client = make_client(handler)
    assert run(client.create_allocation(request_body())).id == 1
    assert len(bodies) == 3 and len(set(bodies)) == 1
    assert b"fw-p1-l1" in bodies[0]


def test_post_gives_up_after_three_attempts():
    calls = []

    def handler(request):
        calls.append(1)
        return fault()

    with pytest.raises(SimulatorUnavailable):
        run(make_client(handler).create_allocation(request_body()))
    assert len(calls) == 3


def test_post_409_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(409, json={"detail": {"code": "INSUFFICIENT_INVENTORY", "message": "no"}})

    with pytest.raises(SimulatorRejected):
        run(make_client(handler).create_allocation(request_body()))
    assert len(calls) == 1


def test_cancel_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return fault()

    with pytest.raises(SimulatorUnavailable):
        run(make_client(handler).cancel_allocation(7))
    assert len(calls) == 1


def test_retry_budget_cuts_off_a_retry_storm():
    calls = []

    def handler(request):
        calls.append(1)
        return fault()

    # A breaker that never opens, so only the budget limits the retries.
    settings = Settings(breaker_consecutive_failures=10_000, breaker_failure_rate=2.0, retry_budget_floor=3)
    client = make_client(handler, settings=settings)
    for _ in range(20):
        with pytest.raises(SimulatorUnavailable):
            run(client.instance())
    assert client.retry_budget_exhausted_total > 0
    # 20 requests allow max(3, 0.4 x 20) = 8 retries, so far fewer than the 40 an unbounded policy would send.
    assert len(calls) <= 20 + 8


def test_open_breaker_refuses_without_sending_and_reports_circuit_open():
    calls = []

    def handler(request):
        calls.append(1)
        return fault()

    clock = FakeClock()
    client = make_client(handler, clock=clock)
    for _ in range(5):  # five requests that fail after their retries
        with pytest.raises(SimulatorUnavailable):
            run(client.instance())
    assert client.breaker_states()["read"]["state"] == "OPEN"
    sent = len(calls)
    with pytest.raises(CircuitOpenError):
        run(client.instance())
    assert len(calls) == sent  # nothing was sent


def test_write_breaker_is_independent_of_read_breaker():
    world = make_world()

    def handler(request):
        if request.method == "POST":
            return fault()
        return httpx.Response(200, json=world.get(request.url.path))

    client = make_client(handler)
    for _ in range(5):
        with pytest.raises(SimulatorUnavailable):
            run(client.create_allocation(request_body()))
    assert client.breaker_states()["write"]["state"] == "OPEN"
    assert run(client.instance()).tick == 0  # reads keep working


def test_breaker_recovers_after_cooldown_with_a_live_simulator():
    state = {"down": True}
    world = make_world()

    def handler(request):
        return fault() if state["down"] else httpx.Response(200, json=world.get(request.url.path))

    clock = FakeClock()
    client = make_client(handler, clock=clock)
    for _ in range(5):
        with pytest.raises(SimulatorUnavailable):
            run(client.instance())
    assert client.breaker_states()["read"]["state"] == "OPEN"
    state["down"] = False
    clock.advance(5.1)
    run(client.instance())
    run(client.instance())
    assert client.breaker_states()["read"]["state"] == "CLOSED"


def test_health_probe_bypasses_breaker_and_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return fault()

    client = make_client(handler)
    with pytest.raises(SimulatorUnavailable):
        run(client.health())
    assert len(calls) == 1
    assert client.breaker_states()["read"]["state"] == "CLOSED"


def test_probe_through_a_half_open_breaker_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return fault()

    clock = FakeClock()
    client = make_client(handler, clock=clock)
    for _ in range(5):
        with pytest.raises(SimulatorUnavailable):
            run(client.instance())
    before = len(calls)
    clock.advance(5.1)
    with pytest.raises(SimulatorUnavailable):
        run(client.instance())
    assert len(calls) == before + 1  # one probe, no retries
    assert client.breaker_states()["read"]["state"] == "OPEN"
    assert client.breaker_states()["read"]["cooldown_seconds"] == 10.0


def test_default_24_percent_error_rate_does_not_open_the_breaker():
    import random

    rnd = random.Random(7)
    world = make_world()

    def handler(request):
        return fault() if rnd.random() < 0.24 else httpx.Response(200, json=world[request.url.path])

    client = make_client(handler)
    failed = 0
    for _ in range(100):
        try:
            run(client.read_world())
        except SimulatorUnavailable:
            failed += 1
    assert client.breaker_states()["read"]["opened_total"] == 0
    assert failed < 40  # most refreshes still succeed


def test_a_cancelled_probe_does_not_leave_the_breaker_stuck():
    clock = FakeClock()
    world = make_world()
    state = {"mode": "fail"}

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if state["mode"] == "fail":
                return fault()
            if state["mode"] == "hang":
                await asyncio.sleep(10)
            return httpx.Response(200, json=world[request.url.path])

    client = SimulatorClient("http://sim", transport=Transport(), clock=clock, sleep=Sleeps())

    async def scenario():
        for _ in range(5):
            with pytest.raises(SimulatorUnavailable):
                await client.instance()
        clock.advance(5.1)
        state["mode"] = "hang"
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.instance(), 0.05)  # the probe is cancelled
        clock.advance(11)
        state["mode"] = "ok"
        await client.instance()  # a new probe is allowed

    run(scenario())


# -- concurrency cap and deadline ----------------------------------------------------


def test_at_most_max_in_flight_requests_are_outstanding():
    state = {"now": 0, "peak": 0}
    world = make_world()

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.01)
            state["now"] -= 1
            return httpx.Response(200, json=world[request.url.path])

    client = SimulatorClient("http://sim", settings=Settings(max_in_flight=3), transport=Slow())

    async def burst():
        await asyncio.gather(*(client.instance() for _ in range(20)))

    run(burst())
    assert state["peak"] == 3


def test_world_read_has_a_deadline():
    world = make_world()

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(0.2)
            return httpx.Response(200, json=world[request.url.path])

    client = SimulatorClient("http://sim", settings=Settings(world_deadline=0.05), transport=Slow())
    with pytest.raises(SimulatorUnavailable, match="exceeded"):
        run(client.read_world())


def test_failed_world_read_leaves_no_task_running():
    world = make_world()

    def handler(request):
        if request.url.path == "/v1/metrics":
            return fault()
        return httpx.Response(200, json=world[request.url.path])

    async def scenario():
        client = make_client(handler)
        with pytest.raises(SimulatorUnavailable):
            await client.read_world()
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        return others

    assert run(scenario()) == []


# -- mode ----------------------------------------------------------------------------


def _snapshot():
    return run(SimulatorClient("http://sim", transport=transport_for(make_world())).read_world())


def test_mode_ladder():
    snap = _snapshot()
    ok = dict(consecutive_failures=0, any_breaker_not_closed=False, stale_after=15.0)
    assert compute_mode(None, **ok) is Mode.STARTING
    assert compute_mode(snap, **ok) is Mode.LIVE
    assert compute_mode(snap, **{**ok, "consecutive_failures": 1}) is Mode.DEGRADED
    assert compute_mode(snap, **{**ok, "any_breaker_not_closed": True}) is Mode.DEGRADED
    assert compute_mode(snap, **{**ok, "stale_after": -1.0}) is Mode.STALE
    assert compute_mode(snap.model_copy(update={"stale": True}), **ok) is Mode.DEGRADED
    assert compute_mode(snap.model_copy(update={"consistent": False}), **ok) is Mode.DEGRADED
