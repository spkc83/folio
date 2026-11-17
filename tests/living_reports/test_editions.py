import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.store import get_store
from living_reports.reports.editions import EditionStore, canonical_hash, render_export
from living_reports.reports.store import ReportStore

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}
TEMPLATE = "<html><head></head><body><!--LR_PAYLOAD--></body></html>"


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def rid(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    return client.post("/api/ask", json={"question": "Where?"}).json()["report_id"]


def editions():
    conn = get_store()
    return EditionStore(conn, ReportStore(conn))


def test_hash_ignores_key_order():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_same_content_freezes_to_one_edition(rid):
    first = editions().freeze(rid)
    second = editions().freeze(rid)
    assert first.number == second.number == 1


def test_changed_content_gets_a_new_number_and_hash(client, rid, fake_llm):
    first = editions().freeze(rid)
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"]}}, {**WORDS, "paragraphs": ["Orders: {fact:q2.order_count}."]})
    client.post("/api/ask", json={"question": "More", "report_id": rid})
    second = editions().freeze(rid)
    assert second.number == 2 and second.content_hash != first.content_hash


def test_edition_stays_the_same_after_the_draft_changes(client, rid, fake_llm):
    first = editions().freeze(rid)
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"]}}, {**WORDS, "paragraphs": ["Orders: {fact:q2.order_count}."]})
    client.post("/api/ask", json={"question": "More", "report_id": rid})
    body = client.get(f"/api/reports/{rid}/editions/1").json()
    assert len(body["spec"]["sections"]) == 1 and body["edition"] == 1
    assert editions().get(rid, 1).content_hash == first.content_hash


def test_edition_records_its_data_time(rid):
    e = editions().freeze(rid)
    assert e.source_id == "duckdb_demo" and e.known_at is not None
    from living_reports.sources.registry import source_for
    assert e.data_time == source_for("duckdb_demo").data_time()


def test_coverage_is_read_through_the_source(client):
    from living_reports.reports.editions import coverage_of
    model = get_store().graph.model()
    assert coverage_of("duckdb_demo", model)["rows"] == 2800


def test_latest_means_published(client, rid):
    store = editions()
    assert client.get(f"/api/reports/{rid}/latest").status_code == 404
    store.freeze(rid)
    assert client.get(f"/api/reports/{rid}/latest").status_code == 404
    store.mark_published(rid, 1)
    assert client.get(f"/api/reports/{rid}/latest").json()["edition"] == 1


def test_unknown_edition_is_404(client, rid):
    assert client.get(f"/api/reports/{rid}/editions/9").status_code == 404


def test_payload_in_the_export_cannot_close_the_script():
    html = render_export({"t": "</script><script>alert(1)</script>"}, TEMPLATE)
    assert html.count("<script>alert") == 0


def test_export_contains_no_order_ids(client, rid, monkeypatch):
    monkeypatch.setenv("LR_EXPORT_TEMPLATE", _template(monkeypatch))
    store = editions()
    store.freeze(rid)
    store.mark_published(rid, 1)
    r = client.get(f"/api/reports/{rid}/editions/1/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "O123" not in r.text and "Loss by channel" in r.text


def test_exploratory_report_has_no_export(client, fake_llm, monkeypatch):
    monkeypatch.setenv("LR_EXPORT_TEMPLATE", _template(monkeypatch))
    fake_llm.push({"sql": "select order_id from orders limit 5"})
    rid = client.post("/api/explore", json={"question": "x"}).json()["report_id"]
    editions().freeze(rid)
    assert client.get(f"/api/reports/{rid}/editions/1/export").status_code == 409


def test_missing_template_gives_a_clear_error(client, rid, monkeypatch):
    monkeypatch.setenv("LR_EXPORT_TEMPLATE", "/nonexistent.html")
    store = editions()
    store.freeze(rid)
    store.mark_published(rid, 1)
    r = client.get(f"/api/reports/{rid}/editions/1/export")
    assert r.status_code == 503 and "build" in r.json()["detail"]


def test_responses_carry_a_strict_policy(client):
    policy = client.get("/api/health").headers["content-security-policy"]
    assert "script-src 'self'" in policy and "default-src 'self'" in policy
    assert "unsafe-eval" not in policy


def _template(monkeypatch):
    import tempfile
    f = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
    f.write(TEMPLATE)
    f.close()
    return f.name


def test_an_edition_keeps_the_cube_and_the_defs(rid):
    from living_reports.reports.editions import payload_of
    edition = editions().freeze(rid)
    assert edition.cube is not None and edition.cube.dimensions == ["orders.sales_channel", "month"]
    payload = payload_of(edition)
    assert payload.cube == edition.cube and payload.query_defs["q1"]["metrics"] == ["fraud_loss"]


def test_an_old_edition_without_a_cube_still_loads(rid):
    from living_reports.reports.editions import Edition
    edition = editions().freeze(rid)
    old = edition.model_dump(mode="json")
    del old["cube"]
    assert Edition.model_validate(old).cube is None


def test_the_export_carries_no_vocabulary(client, rid, monkeypatch):
    import json
    import re
    monkeypatch.setenv("LR_EXPORT_TEMPLATE", _template(monkeypatch))
    store = editions()
    store.freeze(rid)
    store.mark_published(rid, 1)
    html = client.get(f"/api/reports/{rid}/editions/1/export").text
    data = json.loads(re.search(r'id="lr-payload">(.*)</script>', html).group(1))
    assert data["cube"]["dimensions"] == ["orders.sales_channel", "month"]
    assert data["cube"]["vocabulary"] == {} and "orders.payment_method" not in html
