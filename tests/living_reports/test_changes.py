import pytest
from fastapi.testclient import TestClient

from datetime import timedelta

from living_reports.api.app import create_app
from living_reports.reports.certified import list_certified
from living_reports.store import get_store

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
REGION = {**ROUTED, "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]}}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}
RWORDS = {**WORDS, "title": "Orders by region", "paragraphs": ["Orders: {fact:q1_total.order_count}."]}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def rid(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    return client.post("/api/ask", json={"question": "Where?"}, headers={"X-LR-User": "ana"}).json()["report_id"]


def activate(client, fake_llm, reports=1):
    fake_llm.push(*[{"claims": []}] * reports)
    return client.post("/api/sources/duckdb_demo/advance").json()


def by_kind(items, kind):
    return [i for i in items if i["kind"] == kind]


def test_new_column_is_found_and_proposed(client, fake_llm, rid):
    items = activate(client, fake_llm)
    column = by_kind(items, "new_column")[0]
    assert "contract_length_months" in column["title"] and column["proposal_id"]
    assert client.get("/api/graph/proposals").json()


def test_new_value_is_found_with_its_first_date(client, fake_llm, rid):
    value = by_kind(activate(client, fake_llm), "new_value")[0]
    assert "Marketplace" in value["title"] and "2025-12-25" in value["detail"]


def test_affected_reports_come_from_the_graph(client, fake_llm, rid):
    fake_llm.push(REGION, RWORDS)
    other = client.post("/api/ask", json={"question": "Orders?"}).json()["report_id"]
    value = by_kind(activate(client, fake_llm, reports=2), "new_value")[0]
    assert value["affected_reports"] == [rid] and other not in value["affected_reports"]


def test_new_rows_are_reported(client, fake_llm, rid):
    item = by_kind(activate(client, fake_llm), "new_data")[0]
    assert "new orders" in item["title"] and rid in item["affected_reports"]


def test_activating_twice_adds_no_duplicates(client, fake_llm, rid):
    first = activate(client, fake_llm)
    second = client.post("/api/sources/duckdb_demo/advance").json()
    assert sorted(i["id"] for i in first) == sorted(i["id"] for i in second)
    assert len(client.get("/api/changes").json()) == len(first)


def test_team_report_gets_a_pending_edition_with_checks(client, fake_llm, rid):
    activate(client, fake_llm)
    pub = client.get(f"/api/reports/{rid}/publication").json()
    assert pub["edition"]["status"] == "pending" and pub["edition"]["checks"]
    assert client.get(f"/api/reports/{rid}/latest").status_code == 404


def test_refresh_updates_values_and_blocks(client, fake_llm, rid):
    before = client.get(f"/api/reports/{rid}").json()
    activate(client, fake_llm)
    after = client.get(f"/api/reports/{rid}").json()
    from living_reports.sources.registry import source_for
    fact = after["facts"]["q1.fraud_loss.Marketplace"]
    assert fact["source_id"] == "duckdb_demo"
    assert fact["data_time"] == source_for("duckdb_demo").data_time().isoformat()
    assert "q1.fraud_loss.Marketplace" not in before["facts"]
    assert after["spec"]["title"] == before["spec"]["title"]


def test_refresh_rebuilds_the_cube(client, fake_llm, rid):
    before = client.get(f"/api/reports/{rid}").json()
    activate(client, fake_llm)
    after = client.get(f"/api/reports/{rid}").json()
    assert before["cube"]["dimensions"] == ["orders.sales_channel", "month"]
    assert after["cube"]["query_hash"] != before["cube"]["query_hash"]
    assert after["query_defs"]["q1"]["metrics"] == ["fraud_loss"]


def test_new_questions_use_the_new_data(client, fake_llm, rid):
    activate(client, fake_llm)
    fake_llm.push(REGION, RWORDS)
    new = client.post("/api/ask", json={"question": "Orders?"}).json()["report_id"]
    assert client.get(f"/api/reports/{new}").json()["facts"]["q1_total.order_count"]["formatted"] == "3,000"


def test_advance_of_another_source_is_404(client):
    assert client.post("/api/sources/nope/advance").status_code == 404


def test_advance_without_the_demo_source_is_404(client, monkeypatch):
    monkeypatch.delenv("LR_SOURCE_PATH")
    assert client.post("/api/sources/duckdb_demo/advance").status_code == 404


def test_exploratory_report_is_not_refreshed(client, fake_llm):
    fake_llm.push({"sql": "select 1 as x"})
    rid = client.post("/api/explore", json={"question": "x"}).json()["report_id"]
    activate(client, fake_llm, reports=0)
    assert client.get(f"/api/reports/{rid}/publication").json()["edition"] is None


def test_report_node_links_to_its_metrics(client, rid):
    assert f"report:{rid}" in get_store().graph.slice().dependents("fraud_loss")


def test_sources_state_is_exposed(client, fake_llm):
    body = client.get("/api/sources").json()
    assert body["demo_moment"] == 1
    [source] = body["sources"]
    assert source["id"] == "duckdb_demo" and source["dialect"] == "duckdb"
    assert source["supports_as_of"] is False and source["data_time"]
    activate(client, fake_llm, reports=0)
    after = client.get("/api/sources").json()
    assert after["demo_moment"] == 2
    assert after["sources"][0]["data_time"] > source["data_time"]


def test_confirmed_new_column_becomes_a_field(client, fake_llm, rid):
    items = activate(client, fake_llm)
    column = by_kind(items, "new_column")[0]
    r = client.post(f"/api/graph/proposals/{column['proposal_id']}/confirm", json={},
                    headers={"X-LR-User": "omar"})
    assert r.status_code == 200
    fg = get_store().graph
    field = fg.model().field("orders.contract_length_months")
    assert field.type == "decimal" and field.dimension is not None
    impact = client.get(f"/api/graph/proposals/{column['proposal_id']}/impact")
    assert impact.status_code == 409
    # Confirming the new field closes its change item.
    item = next(i for i in client.get("/api/changes").json() if i["id"] == column["id"])
    assert item["status"] == "done"


def test_new_value_detection_uses_the_model(client, fake_llm, rid):
    items = by_kind(activate(client, fake_llm), "new_value")
    marketplace = [i for i in items if i["title"] == "New Sales channel: Marketplace"]
    assert marketplace and marketplace[0]["id"].startswith("duckdb_demo@")
    assert marketplace[0]["id"].endswith(":new_value:orders.sales_channel:Marketplace")
    assert marketplace[0]["affected_reports"] == [rid]


OMAR = {"X-LR-User": "omar"}


def publish_first(client, fake_llm, rid):
    fake_llm.push({"claims": []})
    state = client.post(f"/api/reports/{rid}/editions").json()
    client.post(f"/api/reports/{rid}/editions/1/sign", json={"content_hash": state["content_hash"]}, headers=OMAR)
    assert client.post(f"/api/reports/{rid}/editions/1/publish", headers=OMAR).status_code == 200


def test_new_items_carry_detected_at_and_is_new(client, fake_llm, rid):
    items = activate(client, fake_llm)
    assert items and all(i["detected_at"] and i["is_new"] is True for i in items)
    value = by_kind(items, "new_value")[0]
    assert value["subject"] == {"dimension": "orders.sales_channel", "value": "Marketplace"}
    assert by_kind(items, "new_column")[0]["subject"] == {
        "column": "contract_length_months", "dataset": "orders"}
    assert by_kind(items, "definition")[0]["subject"] == {"metric": "fraud_loss"}
    assert by_kind(items, "new_data")[0]["subject"] == {}


def test_certified_moved_value_is_a_change_item(client, fake_llm, rid):
    publish_first(client, fake_llm, rid)
    [cq] = list_certified(get_store())
    before = client.get(f"/api/reports/{rid}/editions/1").json()["facts"]["q1_total.fraud_loss"]["formatted"]
    items = activate(client, fake_llm)
    after = client.get(f"/api/reports/{rid}").json()["facts"]["q1_total.fraud_loss"]["formatted"]
    assert before != after
    [item] = by_kind(items, "question")
    assert item["title"] == "Certified answer changed: Where?"
    assert f"{before} → {after}" in item["detail"] and "Verified by omar" in item["detail"]
    assert item["affected_reports"] == [rid] and item["subject"] == {"certified_id": cq.id}


def test_unmoved_certified_value_is_no_change_item(client, fake_llm):
    fake_llm.push(REGION, RWORDS)
    rid = client.post("/api/ask", json={"question": "Orders?"}).json()["report_id"]
    publish_first(client, fake_llm, rid)
    from living_reports.harness.builder import AskContext
    from living_reports.reports.certified import check_certified
    from living_reports.reports.store import ReportStore
    store = get_store()
    assert check_certified(AskContext(store=store, reports=ReportStore(store), source_id="duckdb_demo")) == []


def test_done_items_stay_seven_days(client, fake_llm, rid):
    items = activate(client, fake_llm)
    item = by_kind(items, "definition")[0]
    fake_llm.push({"claims": []})
    r = client.post(f"/api/graph/proposals/{item['proposal_id']}/confirm", json={}, headers=OMAR)
    assert r.status_code == 200
    listed = {i["id"]: i for i in client.get("/api/changes").json()}
    assert listed[item["id"]]["status"] == "done"
    store = get_store()
    real = store.clock
    store.clock = lambda: real() + timedelta(days=8)
    listed = {i["id"]: i for i in client.get("/api/changes").json()}
    assert item["id"] not in listed
    assert all(i["status"] == "open" and i["is_new"] is False for i in listed.values())


def test_refresh_rebuilds_by_section_id(store2, fake_llm):
    from living_reports.harness.builder import AskContext, answer
    from living_reports.reports.changes import refresh_report
    from living_reports.reports.store import ReportStore, StoredReport
    from living_reports.spec.models import Bars

    store = store2
    ctx = AskContext(store=store, reports=ReportStore(store), source_id="duckdb_demo")
    fake_llm.push(ROUTED, WORDS, REGION, {**RWORDS, "paragraphs": ["Orders: {fact:q2_total.order_count}."]})
    rid = answer("Where?", None, ctx).report_id
    answer("Orders by region?", rid, ctx)
    # Make the second section s3 / q3, as after a removal of s2, and drop its chart.
    report = ctx.reports.get(rid)
    report.spec.sections[1].id = "s3"
    report.spec.sections[1].blocks = [b for b in report.spec.sections[1].blocks if not isinstance(b, Bars)]
    moved = StoredReport.model_validate_json(report.model_dump_json().replace("q2", "q3"))
    assert refresh_report(moved, ctx)
    after = ctx.reports.get(rid)
    charts = {s.id: [b.query_ref for b in s.blocks if isinstance(b, Bars)] for s in after.spec.sections}
    assert charts == {"s1": ["q1"], "s3": ["q3"]}


def test_a_broken_source_entry_does_not_break_the_source_list(client, monkeypatch, tmp_path):
    import json
    from living_reports.sources.registry import reset_sources
    gone = {"kind": "duckdb", "files": {"t": str(tmp_path / "missing.parquet")}}
    monkeypatch.setenv("LR_SOURCES", json.dumps({"broken": gone}))
    reset_sources()
    response = client.get("/api/sources")
    assert response.status_code == 200
    body = response.json()
    assert body["demo_moment"] == 1
    by_id = {s["id"]: s for s in body["sources"]}
    assert by_id["duckdb_demo"]["dialect"] == "duckdb"
    assert by_id["broken"] == {"id": "broken", "error": "broken: a configured file is missing"}
    assert str(tmp_path) not in response.text


def test_advance_before_the_demo_source_is_built_is_503(client):
    from living_reports.sources.build import demo_source_path
    store = get_store()
    store.execute("DELETE FROM app_state WHERE key = 'demo_moment'")
    demo_source_path().unlink()
    r = client.post("/api/sources/duckdb_demo/advance")
    assert r.status_code == 503
    assert r.json()["detail"] == "The demo source is not built yet. Restart the API to build it."


def test_refresh_keeps_a_value_claim_true_when_only_the_value_moved(client, fake_llm):
    """An unbounded query: new data moves the value, and the claim's words stay true."""
    cited = {**WORDS, "paragraphs": ["In short, {claim:q1.value.fraud_loss}."]}
    fake_llm.push(ROUTED, cited)
    rid = client.post("/api/ask", json={"question": "Where?"}, headers={"X-LR-User": "ana"}).json()["report_id"]
    before = client.get(f"/api/reports/{rid}").json()
    activate(client, fake_llm)
    after = client.get(f"/api/reports/{rid}").json()
    assert after["spec"]["sections"][0]["stale_claims"] == []
    assert after["facts"]["q1_total.fraud_loss"]["value"] != before["facts"]["q1_total.fraud_loss"]["value"]
    # The data's own span is not in a claim's words: only query bounds are.
    assert "(" not in after["spec"]["claims"]["q1.value.fraud_loss"]["text"]


def test_refresh_marks_a_rank_stale_when_a_new_member_arrives(client, fake_llm):
    """The new data adds the Marketplace channel: "of the 3 sales channels" is no longer true."""
    fake_llm.push(ROUTED, {**WORDS, "paragraphs": ["x"]})
    rid = client.post("/api/ask", json={"question": "Where?"}, headers={"X-LR-User": "ana"}).json()["report_id"]
    claims = client.get(f"/api/reports/{rid}").json()["spec"]["claims"]
    rank = next(k for k, c in claims.items() if c["kind"] == "rank")
    r = client.put(f"/api/reports/{rid}/sections/s1/prose", json={"paragraphs": [f"{{claim:{rank}}}."]},
                   headers={"X-LR-User": "ana"})
    assert r.status_code == 200, r.text
    activate(client, fake_llm)
    after = client.get(f"/api/reports/{rid}").json()
    assert after["spec"]["sections"][0]["stale_claims"] == [rank]
