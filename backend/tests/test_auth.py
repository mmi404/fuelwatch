"""Accounts and roles: who may do what, and the name the audit log keeps."""

import httpx
import pytest
from fastapi.testclient import TestClient

from fuelwatch.auth import parse_accounts
from fuelwatch.client import SimulatorClient
from fuelwatch.config import Settings
from fuelwatch.main import create_app

from tests.test_executor import ExecSim, Sleeps, _first_diesel

OPERATOR_KEY, SUPERVISOR_KEY = "rahim-key-000001", "nadia-key-000002"
ACCOUNTS = f"rahim:operator:{OPERATOR_KEY},nadia:supervisor:{SUPERVISOR_KEY}"


def key(value):
    return {"X-Operator-Token": value}


def make_api(**settings):
    sim = ExecSim()
    s = Settings(poll_interval=0.01, ready_max_age=60, database_path=":memory:", **settings)
    client = SimulatorClient("http://sim", transport=httpx.MockTransport(sim.handler), sleep=Sleeps())
    return sim, TestClient(create_app(settings=s, client=client))


def test_accounts_are_read_with_their_roles_and_keys_may_hold_colons():
    found = parse_accounts(" rahim:Operator:abc:def:ghi:jkl , nadia:supervisor:0123456789ab ")
    assert [(a.name, a.role) for a in found.values()] == [("rahim", "operator"), ("nadia", "supervisor")]
    assert found["rahim"].key == "abc:def:ghi:jkl"
    assert "abc:def" not in repr(found)  # a key never shows when an account is printed
    assert parse_accounts("") == {}


@pytest.mark.parametrize(
    "spec",
    [
        "rahim:secretsecret99",  # no role
        "rahim:admin:secretsecret99",  # unknown role
        "rahim:operator:short",
        "system:supervisor:secretsecret99",
        "rahim:operator:secretsecret99,Rahim:supervisor:othersecret999",
        "rahim:operator:secretsecret99,nadia:supervisor:secretsecret99",
        "bad name:operator:secretsecret99",
    ],
)
def test_a_wrong_entry_stops_the_start_and_no_key_is_repeated(spec):
    with pytest.raises(ValueError) as caught:
        parse_accounts(spec)
    assert "secretsecret99" not in str(caught.value)


def test_an_operator_decides_on_shipments_and_nothing_more():
    sim, app = make_api(accounts=ACCOUNTS, operator_token="s3cret")
    with app as api:
        p = _first_diesel(api)
        url = f"/api/proposals/{p['id']}/approve"
        assert api.post(url, headers=key("s3cret")).status_code == 401  # the shared token is off
        for response in (
            api.post("/api/sim/run", headers=key(OPERATOR_KEY)),
            api.put("/api/autopilot", headers=key(OPERATOR_KEY), json={"enabled": True}),
            api.post(url, headers=key(OPERATOR_KEY), json={"override_stale": True}),
        ):
            assert response.status_code == 403 and response.json()["error"]["code"] == "SUPERVISOR_REQUIRED"
        assert sim.posts == [] and api.get("/api/autopilot").json()["enabled"] is False
        assert api.post(url, headers=key(OPERATOR_KEY)).json()["status"] == "EXECUTED"
        approved = [r for r in api.get("/api/audit").json() if r["action"] == "proposal.approved"]
        assert [r["actor"] for r in approved] == ["rahim"]


def test_a_supervisor_may_change_the_autopilot_and_is_named_in_the_audit():
    _, app = make_api(accounts=ACCOUNTS)
    with app as api:
        assert api.put("/api/autopilot", headers=key(SUPERVISOR_KEY), json={"min_confidence": 0.6}).status_code == 200
        changed = [r for r in api.get("/api/audit").json() if r["action"] == "autopilot.changed"]
        assert [r["actor"] for r in changed] == ["nadia"]


def test_the_console_can_ask_who_a_key_belongs_to():
    _, app = make_api(accounts=ACCOUNTS)
    with app as api:
        assert api.get("/api/me", headers=key(OPERATOR_KEY)).json() == {"name": "rahim", "role": "operator"}
        assert api.get("/api/me", headers=key("wrong")).status_code == 401
    _, shared = make_api(operator_token="s3cret")
    with shared as api:
        assert api.get("/api/me", headers=key("s3cret")).json() == {"name": "operator", "role": "supervisor"}
