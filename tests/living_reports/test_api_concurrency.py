import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.reports.editions import EditionStore
from living_reports.reports.store import ReportStore

from .test_store import report

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels",
         "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def test_concurrent_writes_all_land(store):
    reports = ReportStore(store)
    editions = EditionStore(store, reports)

    def work(n: int) -> list[int]:
        numbers = []
        for i in range(4):
            rid = reports.save(report(f"r{n}-{i}")).id
            numbers.append(editions.freeze(rid).number)
        return numbers

    before = store.graph.version()
    with ThreadPoolExecutor(16) as pool:
        results = list(pool.map(work, range(16)))
    assert len(reports.list_reports()) == 64
    assert all(n == 1 for numbers in results for n in numbers)
    s = store.graph.slice()
    for r in reports.list_reports():
        assert s.node(f"report:{r.id}") is not None
        assert s.node(f"edition:{r.id}:1") is not None
        built = s.edges(kind="built_from", from_id=f"edition:{r.id}:1")
        assert [e.to_id for e in built] == [f"report:{r.id}"]
    # Each freeze writes three confirmed rows: the Report node, the Edition node, built_from.
    assert store.graph.version() == before + 64 * 3


def test_api_concurrent_thread_opens(client, fake_llm, caplog):
    fake_llm.push(ROUTED, WORDS)
    rid = client.post("/api/ask", json={"question": "Where?"}).json()["report_id"]

    def open_five(_: int) -> list[int]:
        return [
            client.post(f"/api/reports/{rid}/threads", json={"section_id": "s1"}).status_code
            for _ in range(5)
        ]

    with caplog.at_level(logging.DEBUG), ThreadPoolExecutor(8) as pool:
        statuses = [s for batch in pool.map(open_five, range(8)) for s in batch]
    assert statuses == [200] * 40
    assert len(client.get(f"/api/reports/{rid}/threads").json()) == 40
    assert "locked" not in caplog.text


def test_health_reports_the_store(client):
    assert client.get("/api/health").json() == {
        "status": "ok", "store": "ok", "sources": {"duckdb_demo": "ok"}}


def test_health_is_degraded_when_the_store_fails(client, monkeypatch):
    from living_reports.store import get_store

    def broken(*_):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(get_store(), "row", broken)
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json() == {
        "status": "degraded", "store": "unavailable", "sources": {"duckdb_demo": "ok"}}
    assert "disk gone" not in r.text


def test_sql_string_payload_is_rejected(client):
    body = {
        "node_id": "fraud_loss",
        "kind": "MetricBinding",
        "payload": {"name": "fraud_loss", "label": "x", "format": "currency",
                    "expr": "sum(loss_amount)"},
        "reason": "r",
    }
    r = client.post("/api/graph/proposals", json=body)
    assert r.status_code == 422
    assert "expr" in r.json()["detail"]
    assert client.get("/api/graph/proposals").json() == []


def _propose(client, node_id, kind, payload):
    body = {"node_id": node_id, "kind": kind, "payload": payload, "reason": "r"}
    return client.post("/api/graph/proposals", json=body)


def test_propose_refuses_a_column_with_sql(client):
    payload = {"name": "evil", "dataset": "orders", "column": "x) OR 1=1 --", "type": "string",
               "dimension": {}}
    r = _propose(client, "orders.evil", "Field", payload)
    assert r.status_code == 422
    assert "column" in r.json()["detail"]
    assert client.get("/api/graph/proposals").json() == []


def test_propose_refuses_an_unlisted_kind(client):
    r = _propose(client, "report:x", "Report", {"title": "x", "tier": "team", "author": "ana"})
    assert r.status_code == 422
    assert "Report" in r.json()["detail"]
    source = {"name": "evil", "dialect": "duckdb"}
    assert _propose(client, "evil", "Source", source).status_code == 422


def test_propose_refuses_a_field_expression(client):
    payload = {"name": "calc", "dataset": "orders", "column": "calc", "type": "decimal",
               "expression": {"sql": "loss_amount * 2"}}
    r = _propose(client, "orders.calc", "Field", payload)
    assert r.status_code == 422
    assert "expression" in r.json()["detail"]


def test_propose_accepts_a_plain_field(client):
    payload = {"name": "phone_line_type", "dataset": "orders", "column": "phone_line_type",
               "type": "string", "dimension": {}}
    assert _propose(client, "orders.phone_line_type", "Field", payload).status_code == 200


def test_ask_still_answers_with_a_ratio_metric_in_the_graph(client, fake_llm):
    from living_reports.semantic.kinds import MetricBindingPayload, Ratio
    from living_reports.store import get_store

    get_store().graph.put("loss_per_order", "MetricBinding", MetricBindingPayload(
        name="loss_per_order", label="Loss per order", format="currency",
        ratio=Ratio(numerator="fraud_loss", denominator="order_count"),
    ))
    fake_llm.push(ROUTED, WORDS)
    r = client.post("/api/ask", json={"question": "Where?"})
    assert r.status_code == 200 and r.json()["kind"] == "report"


def test_propose_derives_the_node_id(client):
    payload = {"name": "phone_line_type", "dataset": "orders", "column": "phone_line_type",
               "type": "string", "dimension": {}}
    body = {"kind": "Field", "payload": payload, "reason": "r"}
    r = client.post("/api/graph/proposals", json=body)
    assert r.status_code == 200 and r.json()["change"]["id"] == "orders.phone_line_type"
    wrong = client.post("/api/graph/proposals", json={**body, "node_id": "zzz"})
    assert wrong.status_code == 422
    quoted = client.post("/api/graph/proposals", json={**body, "node_id": 'x" y'})
    assert quoted.status_code == 422
    metric = client.get("/api/graph/proposals").json()
    assert [p["change"]["id"] for p in metric] == ["orders.phone_line_type"]


def test_impact_of_a_new_metric_is_409(client):
    from living_reports.store import get_store

    payload = dict(get_store().graph.slice().node("fraud_loss").payload, name="new_loss")
    p = client.post("/api/graph/proposals",
                    json={"kind": "MetricBinding", "payload": payload, "reason": "r"}).json()
    r = client.get(f"/api/graph/proposals/{p['id']}/impact")
    assert r.status_code == 409 and r.json()["detail"]


def test_impact_of_a_ratio_change_has_a_preview(client):
    # The query engine runs ratio metrics, so a ratio definition previews like a measure.
    payload = {"name": "fraud_loss", "label": "Fraud loss", "format": "currency",
               "ratio": {"numerator": "order_value", "denominator": "order_count"}}
    p = client.post("/api/graph/proposals",
                    json={"kind": "MetricBinding", "payload": payload, "reason": "r"}).json()
    r = client.get(f"/api/graph/proposals/{p['id']}/impact")
    assert r.status_code == 200, r.text
    assert r.json()["before"]["formatted"] != r.json()["after"]["formatted"]


def test_impact_of_a_ratio_on_itself_is_409(client):
    payload = {"name": "fraud_loss", "label": "Fraud loss", "format": "currency",
               "ratio": {"numerator": "fraud_loss", "denominator": "order_count"}}
    p = client.post("/api/graph/proposals",
                    json={"kind": "MetricBinding", "payload": payload, "reason": "r"}).json()
    r = client.get(f"/api/graph/proposals/{p['id']}/impact")
    assert r.status_code == 409 and r.json()["detail"]


def _expression_metric(sql: str, ast=None) -> dict:
    return {"name": "net_expr", "label": "Net", "format": "currency",
            "expression": {"sql": sql, "dialect": "duckdb", "dataset": "orders",
                           "fields": [], "ast": ast if ast is not None else []}}


def test_propose_parses_an_expression_metric_on_the_server(client):
    sql = "sum(orders.loss_amount) filter (where orders.fraud_type <> 'friendly_fraud')"
    r = _propose(client, "net_expr", "MetricBinding", _expression_metric(sql))
    assert r.status_code == 200, r.text
    expression = r.json()["change"]["payload"]["expression"]
    assert expression["fields"] == ["orders.fraud_type", "orders.loss_amount"]
    assert expression["ast"]


def test_propose_refuses_an_invalid_expression_metric(client):
    r = _propose(client, "net_expr", "MetricBinding",
                 _expression_metric("sum(loss_amount); drop table orders"))
    assert r.status_code == 422
    assert "SELECT" not in r.json()["detail"] and r.json()["detail"]
    r = _propose(client, "net_expr", "MetricBinding", _expression_metric("max(getenv('HOME'))"))
    assert r.status_code == 422
    assert "getenv" in r.json()["detail"].lower()
    assert client.get("/api/graph/proposals").json() == []


def test_propose_refuses_an_expression_in_an_unknown_dialect(client):
    body = _expression_metric("sum(orders.loss_amount)")
    body["expression"]["dialect"] = "mysql"
    r = _propose(client, "net_expr", "MetricBinding", body)
    assert r.status_code == 422 and "dialect" in r.json()["detail"]


def test_propose_refuses_read_csv_in_an_expression(client):
    r = _propose(client, "net_expr", "MetricBinding",
                 _expression_metric("max(read_csv('/etc/passwd'))"))
    assert r.status_code == 422 and "not allowed" in r.json()["detail"]
