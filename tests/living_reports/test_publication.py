import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def rid(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    return client.post("/api/ask", json={"question": "Where?"}).json()["report_id"]


def test_new_report_has_no_edition(client, rid):
    body = client.get(f"/api/reports/{rid}/publication").json()
    assert body["edition"] is None and body["draft_changed"] is True


def test_publication_shows_the_newest_edition(client, fake_llm, rid):
    fake_llm.push({"claims": []})
    client.post(f"/api/reports/{rid}/editions")
    body = client.get(f"/api/reports/{rid}/publication").json()
    assert body["edition"]["number"] == 1 and body["draft_changed"] is False


def test_a_changed_draft_is_reported(client, fake_llm, rid):
    fake_llm.push({"claims": []})
    client.post(f"/api/reports/{rid}/editions")
    client.post(f"/api/reports/{rid}/tier", json={"tier": "business_critical"}, headers={"X-LR-User": "omar"})
    assert client.get(f"/api/reports/{rid}/publication").json()["draft_changed"] is True


def test_report_payload_carries_the_cost(client, rid):
    cost = client.get(f"/api/reports/{rid}").json()["cost"]
    assert cost["calls"] == 2


def test_unknown_report_has_no_publication(client):
    assert client.get("/api/reports/nope/publication").status_code == 404
