import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.store import get_store
from living_reports.semantic.demo import seed_demo
from living_reports.harness.context import build_context
from living_reports.reports.store import ReportStore

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def report_id(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    return client.post("/api/ask", json={"question": "Where?"}).json()["report_id"]


def open_thread(client, rid):
    return client.post(f"/api/reports/{rid}/threads", json={"section_id": "s1", "fact_id": "q1_total.fraud_loss"}).json()


def test_open_thread_stays_on_its_fact(client, report_id):
    t = open_thread(client, report_id)
    assert t["fact_id"] == "q1_total.fraud_loss" and t["status"] == "open"


def test_thread_on_unknown_section_is_rejected(client, report_id):
    assert client.post(f"/api/reports/{report_id}/threads", json={"section_id": "nope"}).status_code == 404


def test_message_gets_a_reply_with_traced_values(client, report_id, fake_llm):
    t = open_thread(client, report_id)
    fake_llm.push({"reply": "The total is {fact:q1_total.fraud_loss}."})
    m = client.post(f"/api/threads/{t['id']}/messages", json={"text": "Why this total?"}).json()
    assert m["messages"][-1]["role"] == "assistant" and "{fact:" in m["messages"][-1]["text"]


def test_raw_number_in_a_reply_is_replaced(client, report_id, fake_llm):
    t = open_thread(client, report_id)
    fake_llm.push({"reply": "It is 99%."}, {"reply": "It is 99%."})
    m = client.post(f"/api/threads/{t['id']}/messages", json={"text": "?"}).json()
    assert "99" not in m["messages"][-1]["text"]


def test_close_leaves_a_decision_on_the_section(client, report_id):
    t = open_thread(client, report_id)
    client.post(f"/api/threads/{t['id']}/close", json={"decision": "Telesales needs a review."})
    body = client.get(f"/api/reports/{report_id}").json()
    entry = body["spec"]["sections"][0]["decisions"][0]
    assert "Telesales needs a review." in entry and t["id"] in entry


def test_closed_thread_rejects_messages(client, report_id):
    t = open_thread(client, report_id)
    client.post(f"/api/threads/{t['id']}/close", json={"decision": "done"})
    assert client.post(f"/api/threads/{t['id']}/messages", json={"text": "x"}).status_code == 409


def test_threads_are_listed_with_the_report(client, report_id):
    t = open_thread(client, report_id)
    assert client.get(f"/api/reports/{report_id}/threads").json()[0]["id"] == t["id"]


def test_promote_adds_a_section(client, report_id, fake_llm):
    t = open_thread(client, report_id)
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]}},
                  {**WORDS, "paragraphs": ["Orders were {fact:q2_total.order_count}."], "heading": "Volume"})
    r = client.post(f"/api/threads/{t['id']}/promote", json={"question": "Orders by region"}).json()
    assert r["kind"] == "report"
    assert len(client.get(f"/api/reports/{report_id}").json()["spec"]["sections"]) == 2


@pytest.fixture
def stores(orders_file):
    conn = get_store()
    graph = conn
    seed_demo(graph.graph)
    return graph, ReportStore(conn)


def test_context_has_the_section_and_its_graph_nodes(client, report_id, fake_llm):
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]}},
                  {**WORDS, "paragraphs": ["Orders were {fact:q2_total.order_count}."], "heading": "Volume"})
    client.post("/api/ask", json={"question": "More", "report_id": report_id})
    conn = get_store()
    report = ReportStore(conn).get(report_id)
    pack = build_context(report, "s1", None, conn.graph, [])
    assert "Channels" in pack.text and "Volume" not in pack.text
    assert "Total loss on orders" in pack.text      # the linked metric definition
    assert "q2_total.order_count" not in pack.text


def test_context_omits_closed_transcripts_but_keeps_decisions(client, report_id):
    t = open_thread(client, report_id)
    client.post(f"/api/threads/{t['id']}/close", json={"decision": "Keep watching."})
    conn = get_store()
    pack = build_context(ReportStore(conn).get(report_id), "s1", None, conn.graph, [])
    assert "Keep watching." in pack.text


def test_context_stays_within_budget(client, report_id):
    conn = get_store()
    report = ReportStore(conn).get(report_id)
    long = [{"role": "user", "text": "word " * 2000}] * 5
    pack = build_context(report, "s1", None, conn.graph, long, budget_tokens=500)
    assert pack.estimated_tokens <= 500


def test_closing_a_closed_thread_is_rejected_and_adds_no_decision(client, report_id):
    t = open_thread(client, report_id)
    client.post(f"/api/threads/{t['id']}/close", json={"decision": "Review Telesales."})
    again = client.post(f"/api/threads/{t['id']}/close", json={"decision": "Review Telesales."})
    assert again.status_code == 409
    section = client.get(f"/api/reports/{report_id}").json()["spec"]["sections"][0]
    assert len(section["decisions"]) == 1


def test_decision_with_a_raw_number_is_rejected(client, report_id):
    t = open_thread(client, report_id)
    r = client.post(f"/api/threads/{t['id']}/close", json={"decision": "Loss is 12 million, cut it."})
    assert r.status_code == 422
    section = client.get(f"/api/reports/{report_id}").json()["spec"]["sections"][0]
    assert section["decisions"] == []
    assert client.post(f"/api/threads/{t['id']}/close", json={"decision": "Cut it."}).status_code == 200


def test_thread_is_a_graph_node_with_versions(client, report_id):
    from living_reports.store import get_store
    t = open_thread(client, report_id)
    fg = get_store().graph
    node = fg.slice().node(f"thread:{t['id']}")
    assert node.payload["status"] == "open" and node.version == 1
    edges = fg.slice().edges(kind="discussed_in", from_id=f"thread:{t['id']}")
    assert [e.to_id for e in edges] == [f"report:{report_id}"]
    client.post(f"/api/threads/{t['id']}/close", json={"decision": "Keep watching the channel."},
                headers={"X-LR-User": "omar"})
    closed = fg.slice().node(f"thread:{t['id']}")
    assert closed.payload["status"] == "closed" and closed.version == 2
    assert closed.payload["decision"] == "Keep watching the channel."
