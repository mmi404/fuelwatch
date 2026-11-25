import httpx
from fastapi.testclient import TestClient

from fuelwatch.allocate import plan_shipments
from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.db import Database
from fuelwatch.main import create_app
from fuelwatch.proposals import EXPIRY_TICKS, ProposalService
from fuelwatch.stockout import assess, what_if
from fuelwatch.world import WorldTracker

from .conftest import Sleeps
from .test_allocate import depot, forecaster_for, route, snapshot
from .test_world import FakeSim


class Rig:
    """A world tracker, a database and the proposal service, fed snapshots by hand."""

    def __init__(self) -> None:
        self.db = Database(":memory:")
        client = SimulatorClient("http://sim", transport=httpx.MockTransport(lambda r: httpx.Response(500)), sleep=Sleeps())
        self.world = WorldTracker(self.db, client)
        self.service = ProposalService(self.db, self.world)

    def step(self, snap):
        self.world.observe(snap)
        f = forecaster_for(snap)
        assessments = assess(snap, f)
        self.service.sync_alerts(snap, assessments)
        return self.service.sync(snap, f, assessments, plan_shipments(snap, f, assessments))


def diesel_proposals(rig, **kw):
    return [p for p in rig.service.recent(**kw) if p["fuel_type"] == "DIESEL"]


def test_a_low_station_gets_one_proposal_with_stored_keys_and_a_full_card():
    rig = Rig()
    counts = rig.step(snapshot(diesel_inv=1500))
    assert counts["created"] == 1
    (p,) = diesel_proposals(rig)
    assert p["status"] == "PROPOSED" and p["policy"] == "greedy" and p["expires_tick"] == p["snapshot_tick"] + EXPIRY_TICKS
    (line,) = p["lines"]
    assert line["status"] == "PLANNED" and line["idempotency_key"] == f"fw-p{p['id']}-l{line['id']}"
    card = p["explanation"]
    assert {"headline", "action", "effect", "signals", "binding_constraints", "shortfalls", "alternatives", "confidence"} <= set(card)
    assert card["action"]["total_liters"] == line["proposed_quantity"]
    assert card["effect"]["risk_after"] < card["effect"]["risk_before"]
    assert card["effect"]["unmet_avoided_p90_liters"] > 0
    assert p["risk_after"] == card["effect"]["risk_after"]


def test_an_unchanged_plan_keeps_the_proposal_instead_of_churning():
    rig = Rig()
    rig.step(snapshot(diesel_inv=1500))
    first = diesel_proposals(rig)[0]["id"]
    counts = rig.step(snapshot(diesel_inv=1500))
    assert counts["kept"] == 1 and counts["created"] == 0
    assert [p["id"] for p in diesel_proposals(rig)] == [first]


def test_a_materially_different_plan_supersedes_the_old_proposal():
    rig = Rig()
    rig.step(snapshot(diesel_inv=1500))
    old = diesel_proposals(rig)[0]
    rig.step(snapshot(diesel_inv=100))  # much lower stock: a much larger shipment
    new, replaced = diesel_proposals(rig)[0], diesel_proposals(rig)[1]
    assert replaced["id"] == old["id"] and replaced["status"] == "SUPERSEDED" and f"proposal {new['id']}" in replaced["status_reason"]
    assert new["status"] == "PROPOSED" and new["lines"][0]["proposed_quantity"] > old["lines"][0]["proposed_quantity"]


def test_a_station_that_no_longer_needs_fuel_supersedes_its_proposal():
    rig = Rig()
    rig.step(snapshot(diesel_inv=1500))
    rig.step(snapshot(diesel_inv=14000))
    (p,) = diesel_proposals(rig)
    assert p["status"] == "SUPERSEDED" and p["status_reason"] == "no longer recommended"


def test_a_proposal_expires_after_its_expiry_tick():
    rig = Rig()
    rig.step(snapshot(diesel_inv=1500, tick=0))
    counts = rig.step(snapshot(diesel_inv=14000, tick=EXPIRY_TICKS))
    (p,) = diesel_proposals(rig)
    assert p["status"] == "EXPIRED" and counts["expired"] == 1


def test_a_world_reset_expires_open_proposals_and_clears_alerts():
    rig = Rig()
    rig.step(snapshot(diesel_inv=1500, tick=5))
    assert rig.service.alerts(("open",))
    rig.step(snapshot(diesel_inv=14000, tick=0))  # tick moved backwards: a reset
    assert rig.world.resets_total == 1
    assert all(p["status"] == "EXPIRED" and p["status_reason"] == "world reset" for p in rig.db.proposals(epoch_id=1))
    assert rig.db.alerts(epoch_id=1, states=("open", "acknowledged")) == []
    assert rig.service.recent() == []  # the new world starts with none


def test_a_stale_snapshot_creates_no_new_advice():
    rig = Rig()
    stale = snapshot(diesel_inv=1500).model_copy(update={"stale": True})
    assert rig.step(stale)["created"] == 0
    assert rig.service.recent() == []


def test_alerts_open_escalate_and_clear():
    rig = Rig()
    rig.step(snapshot(diesel_inv=3200))  # about 32 ticks at 100 L: WATCH or better
    rig.step(snapshot(diesel_inv=1500, tick=1))
    rig.step(snapshot(diesel_inv=300, tick=2))
    alerts = [a for a in rig.service.alerts() if a["fuel_type"] == "DIESEL"]
    assert len(alerts) == 1 and alerts[0]["state"] == "open" and alerts[0]["level"] == "CRITICAL" and alerts[0]["first_tick"] <= 1
    rig.step(snapshot(diesel_inv=14000, tick=3))
    assert [a["state"] for a in rig.service.alerts() if a["fuel_type"] == "DIESEL"] == ["cleared"]


def test_an_acknowledged_alert_reopens_when_it_gets_worse():
    rig = Rig()
    rig.step(snapshot(diesel_inv=3200))
    (a,) = [a for a in rig.service.alerts() if a["fuel_type"] == "DIESEL"]
    rig.db.set_alert_state(a["id"], "acknowledged")
    rig.step(snapshot(diesel_inv=3200, tick=1))
    assert [x["state"] for x in rig.service.alerts() if x["fuel_type"] == "DIESEL"] == ["acknowledged"]  # same level, stays acknowledged
    rig.step(snapshot(diesel_inv=200, tick=2))
    assert [x["state"] for x in rig.service.alerts() if x["fuel_type"] == "DIESEL"] == ["open"]


def test_the_card_lists_alternatives_measured_with_the_same_estimator():
    rig = Rig()
    snap = snapshot(
        diesel_inv=1500,
        depots=(depot("depot-gazipur"), depot("depot-narayanganj")),
        routes=(route("route-gazipur-mirpur", "depot-gazipur", tau=2), route("route-narayanganj-mirpur", "depot-narayanganj", tau=6)),
    )
    rig.step(snap)
    card = diesel_proposals(rig)[0]["explanation"]
    by_option = {a["option"]: a for a in card["alternatives"]}
    assert {"do_nothing", "wait_one_tick", "other_route"} <= set(by_option)
    assert by_option["other_route"]["route_id"] == "route-narayanganj-mirpur" and by_option["other_route"]["arrival_tick"] == 6
    # doing nothing is never better than acting, and later is never better than sooner
    assert by_option["do_nothing"]["outlook"]["unmet_p90_liters"] >= by_option["wait_one_tick"]["outlook"]["unmet_p90_liters"] >= 0


def test_confidence_drops_when_there_is_little_history():
    rig = Rig()
    snap = snapshot(diesel_inv=1500)
    rig.world.observe(snap)
    f = forecaster_for(snap, ticks=3)  # a few observations only
    assessments = assess(snap, f)
    rig.service.sync(snap, f, assessments, plan_shipments(snap, f, assessments))
    conf = diesel_proposals(rig)[0]["explanation"]["confidence"]
    assert conf["score"] == 0.7 and conf["level"] == "Medium" and "less than one day" in conf["reasons"][0]


def test_what_if_extra_fuel_lowers_risk_and_moves_the_stockout_later():
    snap = snapshot(diesel_inv=1500)
    f = forecaster_for(snap)
    base = what_if(snap, f, "station-mirpur", "DIESEL")
    more = what_if(snap, f, "station-mirpur", "DIESEL", {2: 3000.0})
    assert more.risk < base.risk and more.unmet_p90 < base.unmet_p90
    assert base.hours_p90 is not None and (more.hours_p90 is None or more.hours_p90 > base.hours_p90)
    assert what_if(snap, f, "station-nowhere", "DIESEL") is None


# -- API -----------------------------------------------------------------------------


def test_api_serves_proposals_alerts_and_404s(monkeypatch):
    sim = FakeSim()
    sim.step(8)
    settings = Settings(poll_interval=0.01, ready_max_age=60, database_path=":memory:")
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    with TestClient(create_app(settings=settings, client=client)) as api:
        import time

        end = time.time() + 3
        while time.time() < end and not api.get("/api/risks").json():
            time.sleep(0.02)
        assert api.get("/api/proposals").status_code == 200
        assert api.get("/api/proposals", params={"limit": 0}).json()["error"]["code"] == "VALIDATION_ERROR"
        missing = api.get("/api/proposals/9999")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
        assert isinstance(api.get("/api/alerts").json(), list)
        assert api.get("/api/alerts", params={"state": "open,acknowledged"}).status_code == 200
