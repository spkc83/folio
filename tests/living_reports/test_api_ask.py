import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app

ROUTED = {"objective": "status", "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Orders by region", "heading": "Volume", "paragraphs": ["Orders reached {fact:q1_total.order_count}."], "action": None}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def test_ask_then_read_the_report(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    r = client.post("/api/ask", json={"question": "How are we doing?"}).json()
    assert r["kind"] == "report" and r["view"] == "dashboard"
    body = client.get(f"/api/reports/{r['report_id']}").json()
    assert body["tier"] == "team" and body["queries"]["q1"]["points"]


def test_ask_uses_the_current_data(client, fake_llm):
    # Marketplace starts after demo moment 1 ends, so the current data has no matching rows.
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"], "filters": [{"member": "orders.sales_channel", "op": "eq", "values": ["Marketplace"]}]}})
    r = client.post("/api/ask", json={"question": "Marketplace orders?"}).json()
    assert r["kind"] == "out_of_scope" and "No rows match" in r["message"]


def test_report_list(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    client.post("/api/ask", json={"question": "x"})
    assert [r["title"] for r in client.get("/api/reports").json()] == ["Orders by region"]


def test_explore_route(client, fake_llm):
    fake_llm.push({"sql": "select 1 as one"})
    r = client.post("/api/explore", json={"question": "x"}).json()
    assert client.get(f"/api/reports/{r['report_id']}").json()["tier"] == "exploratory"


def test_selection_narrows_the_answer(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    rid = client.post("/api/ask", json={"question": "How are we doing?"}).json()["report_id"]
    loss = {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]}
    fake_llm.push({**ROUTED, "objective": "why", "query": loss},
                  {**WORDS, "paragraphs": ["Loss: {fact:q2_total.fraud_loss}."]})
    telesales = {"member": "orders.sales_channel", "op": "eq", "values": ["Telesales"]}
    r = client.post("/api/ask", json={"question": "And loss?", "report_id": rid,
                                      "selection": {"filters": [telesales], "range": None}}).json()
    assert r["kind"] == "report"
    body = client.get(f"/api/reports/{rid}").json()
    assert telesales in body["query_defs"]["q2"]["filters"]
