import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}
OMAR = {"X-LR-User": "omar"}


def setup_report(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    rid = client.post("/api/ask", json={"question": "Where?"}, headers={"X-LR-User": "ana"}).json()["report_id"]
    fake_llm.push({"claims": []}, {"claims": []})
    state = client.post(f"/api/reports/{rid}/editions").json()
    client.post(f"/api/reports/{rid}/editions/1/sign", json={"content_hash": state["content_hash"]}, headers=OMAR)
    client.post(f"/api/reports/{rid}/editions/1/publish", headers=OMAR)
    changes = client.post("/api/sources/duckdb_demo/advance").json()
    return rid, next(c for c in changes if c["kind"] == "definition")


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def test_definition_change_waits_for_approval(client, fake_llm):
    rid, item = setup_report(client, fake_llm)
    assert item["proposal_id"] and rid in item["affected_reports"]
    pub = client.get(f"/api/reports/{rid}/latest").json()
    assert pub["facts"]["q1_total.fraud_loss"]["metric_version"] == 1


def test_impact_shows_before_and_after(client, fake_llm):
    rid, item = setup_report(client, fake_llm)
    impact = client.get(f"/api/graph/proposals/{item['proposal_id']}/impact").json()
    assert impact["reports"] == [rid]
    assert impact["before"]["formatted"] == "$7.5K" and impact["after"]["formatted"] == "$3.8K"


def confirm(client, fake_llm, item):
    fake_llm.push({"claims": []})
    return client.post(f"/api/graph/proposals/{item['proposal_id']}/confirm", json={}, headers=OMAR)


def test_confirm_creates_a_new_pending_edition(client, fake_llm):
    rid, item = setup_report(client, fake_llm)
    before = client.get(f"/api/reports/{rid}/publication").json()["edition"]["number"]
    assert confirm(client, fake_llm, item).status_code == 200
    pub = client.get(f"/api/reports/{rid}/publication").json()["edition"]
    assert pub["number"] == before + 1 and pub["status"] == "pending" and pub["signatures"] == []
    draft = client.get(f"/api/reports/{rid}").json()
    assert draft["facts"]["q1_total.fraud_loss"]["metric_version"] == 2


def test_published_edition_keeps_its_old_value(client, fake_llm):
    rid, item = setup_report(client, fake_llm)
    old = client.get(f"/api/reports/{rid}/editions/1").json()["facts"]["q1_total.fraud_loss"]["formatted"]
    confirm(client, fake_llm, item)
    assert client.get(f"/api/reports/{rid}/editions/1").json()["facts"]["q1_total.fraud_loss"]["formatted"] == old


def test_change_item_closes_after_confirm(client, fake_llm):
    _, item = setup_report(client, fake_llm)
    confirm(client, fake_llm, item)
    # A done item stays in the feed for seven days, marked done.
    listed = {c["id"]: c for c in client.get("/api/changes").json()}
    assert listed[item["id"]]["status"] == "done"


def test_diff_lists_changed_facts_and_definitions(client, fake_llm):
    rid, item = setup_report(client, fake_llm)
    confirm(client, fake_llm, item)
    newest = client.get(f"/api/reports/{rid}/publication").json()["edition"]["number"]
    diff = client.get(f"/api/reports/{rid}/diff", params={"from": 1, "to": newest}).json()
    changed = {c["id"]: c for c in diff["changed_facts"]}
    assert changed["q1_total.fraud_loss"]["before"] != changed["q1_total.fraud_loss"]["after"]
    assert diff["definition_changes"][0]["metric"] == "fraud_loss"
    assert diff["definition_changes"][0]["from_version"] == 1 and diff["definition_changes"][0]["to_version"] == 2


def test_diff_lists_added_facts(client, fake_llm):
    rid, _ = setup_report(client, fake_llm)
    newest = client.get(f"/api/reports/{rid}/publication").json()["edition"]["number"]
    diff = client.get(f"/api/reports/{rid}/diff", params={"from": 1, "to": newest}).json()
    assert any("Marketplace" in c["label"] for c in diff["added_facts"])


def test_diff_of_unknown_edition_is_404(client, fake_llm):
    rid, _ = setup_report(client, fake_llm)
    assert client.get(f"/api/reports/{rid}/diff", params={"from": 1, "to": 9}).status_code == 404


@pytest.mark.demo_data
def test_real_data_impact(real_store, fake_llm):
    from living_reports.sources.build import build_demo_source
    build_demo_source(1)  # the briefing starts at moment 1
    with TestClient(create_app()) as c:
        fake_llm.push(ROUTED, WORDS)
        rid = c.post("/api/ask", json={"question": "Where?"}).json()["report_id"]
        fake_llm.push({"claims": []})
        changes = c.post("/api/sources/duckdb_demo/advance").json()
        item = next(x for x in changes if x["kind"] == "definition")
        impact = c.get(f"/api/graph/proposals/{item['proposal_id']}/impact").json()
    assert rid in impact["reports"]
    assert impact["before"]["formatted"] == "$52.6M" and impact["after"]["formatted"] == "$43.1M"
