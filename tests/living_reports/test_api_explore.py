import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels",
         "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def rid(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    return client.post("/api/ask", json={"question": "Where?"}).json()["report_id"]


def test_drill_breaks_a_member_down(client, rid):
    r = client.post(f"/api/reports/{rid}/drill",
                    json={"ref": "q1", "by": "orders.fraud_type", "member": {"orders.sales_channel": "Telesales"}})
    assert r.status_code == 200
    body = r.json()
    assert {p["label"] for p in body["points"]} <= {"none", "identity_theft", "friendly_fraud"}
    report = client.get(f"/api/reports/{rid}").json()
    telesales = next(p for p in report["queries"]["q1"]["points"] if p["label"] == "Telesales")
    assert sum(p["value"] or 0 for p in body["points"]) == pytest.approx(telesales["value"])


def test_drill_rejects_an_unknown_dimension(client, rid):
    r = client.post(f"/api/reports/{rid}/drill", json={"ref": "q1", "by": "orders.store", "member": {}})
    assert r.status_code == 422
    assert client.post(f"/api/reports/{rid}/drill", json={"ref": "q9"}).status_code == 422
    assert client.post("/api/reports/nope/drill", json={"ref": "q1"}).status_code == 404


def test_drill_rejects_a_null_member(client, rid):
    r = client.post(f"/api/reports/{rid}/drill", json={"ref": "q1", "by": "dealers.region", "member": {"orders.fraud_type": None}})
    assert r.status_code == 422
    assert r.json()["detail"] == "A member with no value cannot be queried"


def test_pin_writes_a_draft_section(client, fake_llm, rid):
    fake_llm.push({"claims": []})
    assert client.post(f"/api/reports/{rid}/editions").status_code == 200
    assert client.get(f"/api/reports/{rid}/publication").json()["draft_changed"] is False
    fake_llm.push(WORDS)
    sq = {"ref": "q1", "filters": [{"member": "orders.sales_channel", "op": "eq", "values": ["Telesales"]}]}
    r = client.post(f"/api/reports/{rid}/pin", json=sq).json()
    assert r["kind"] == "report"
    report = client.get(f"/api/reports/{rid}").json()
    assert len(report["spec"]["sections"]) == 2
    assert report["spec"]["sections"][1]["intent"] == "Fraud loss · Sales channel = Telesales"
    assert {"member": "orders.sales_channel", "op": "eq", "values": ["Telesales"]} in report["query_defs"]["q2"]["filters"]
    assert report["query_defs"]["q2"].get("dimensions", []) == []
    assert client.get(f"/api/reports/{rid}/publication").json()["draft_changed"] is True


def test_pin_rejects_a_null_member(client, rid):
    r = client.post(f"/api/reports/{rid}/pin", json={"ref": "q1", "member": {"orders.fraud_type": None}})
    assert r.status_code == 422


def test_drill_and_pin_need_the_same_identity_as_reading_a_report(client, rid, monkeypatch):
    monkeypatch.setenv("LR_DEMO_MODE", "false")
    assert client.get(f"/api/reports/{rid}").status_code == 401
    assert client.post(f"/api/reports/{rid}/drill", json={"ref": "q1", "by": "dealers.region"}).status_code == 401
    assert client.post(f"/api/reports/{rid}/pin", json={"ref": "q1"}).status_code == 401


def test_a_report_without_a_cube_says_why(client, fake_llm):
    routed = {**ROUTED, "query": {**ROUTED["query"], "time": {"start": "2025-01-15", "end": "2025-03-01"}}}
    fake_llm.push(routed, WORDS)
    rid = client.post("/api/ask", json={"question": "Mid month?"}).json()["report_id"]
    body = client.get(f"/api/reports/{rid}").json()
    assert body["cube"] is None and body["cube_reason"].startswith("A query has a date bound that is not on day 1")


def test_pin_and_drill_run_on_the_report_source(client, fake_llm, rid):
    from living_reports.reports.store import ReportStore
    from living_reports.store import get_store
    report = client.get(f"/api/reports/{rid}").json()
    assert {f["source_id"] for f in report["facts"].values()} == {"duckdb_demo"}
    assert report["cube"]["source_id"] == "duckdb_demo"
    # A report on a source that is not configured: drill and pin say so and run nothing.
    reports = ReportStore(get_store())
    stored = reports.get(rid)
    stored.source_id = "gone"
    reports.save(stored)
    sq = {"ref": "q1", "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}]}
    drilled = client.post(f"/api/reports/{rid}/drill", json={**sq, "by": "dealers.region"})
    assert drilled.status_code == 503 and drilled.json()["detail"] == "The data source gone is not available."
    pinned = client.post(f"/api/reports/{rid}/pin", json=sq).json()
    assert pinned["kind"] == "failed" and pinned["message"] == "The data source gone is not available."


def test_a_follow_up_after_new_data_rebuilds_the_cube(client, fake_llm, rid):
    from living_reports.sources.build import build_demo_source
    before = client.get(f"/api/reports/{rid}").json()["cube"]
    assert max(r[before["columns"].index("month")] for r in before["rows"]) <= "2025-11-01"
    build_demo_source(2)
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]}}, WORDS)
    client.post("/api/ask", json={"question": "And orders?", "report_id": rid})
    after = client.get(f"/api/reports/{rid}").json()["cube"]
    assert after["query_hash"] != before["query_hash"] and after["data_time"] > before["data_time"]


def test_the_payload_sends_definitions_in_the_engine_form(client, rid):
    from living_reports.reports.store import ReportStore
    from living_reports.store import get_store
    reports = ReportStore(get_store())
    report = reports.get(rid)
    report.query_defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": ["sales_channel"], "top": 3}}
    reports.save(report)
    defs = client.get(f"/api/reports/{rid}").json()["query_defs"]
    assert defs["q1"] == {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"], "filters": [], "limit": 3}


def test_drill_on_a_monthly_section_matches_the_export_drill(client, fake_llm):
    monthly = {**ROUTED, "query": {"metrics": ["fraud_loss"], "time": {"grain": "month"}}}
    fake_llm.push(monthly, {**WORDS, "paragraphs": ["ok"]})
    rid = client.post("/api/ask", json={"question": "Trend?"}).json()["report_id"]
    r = client.post(f"/api/reports/{rid}/drill", json={"ref": "q1", "by": "orders.fraud_type", "member": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["orders.fraud_type", "fraud_loss"]
    # One row per fraud type, as the export drill groups by [by] only; together they are the total.
    assert len(body["rows"]) == len({row[0] for row in body["rows"]})
    total = client.get(f"/api/reports/{rid}").json()["facts"]["q1_total.fraud_loss"]["value"]
    assert sum(row[1] or 0 for row in body["rows"]) == pytest.approx(total)


def test_a_range_outside_the_report_dates_is_one_sentence(client, fake_llm):
    bounded = {**ROUTED, "query": {**ROUTED["query"], "time": {"start": "2025-01-01", "end": "2025-04-01"}}}
    fake_llm.push(bounded, WORDS)
    rid = client.post("/api/ask", json={"question": "Q1?"}).json()["report_id"]
    r = client.post(f"/api/reports/{rid}/drill", json={"ref": "q1", "range": ["2025-07-01", "2025-09-01"]})
    assert r.status_code == 422 and r.json()["detail"] == "The range is outside the report's dates."
