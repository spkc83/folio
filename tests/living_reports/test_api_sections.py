"""Section routes: chip edits, chart forms, commands, prose edits, blocks, feedback."""
import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels",
         "paragraphs": ["App leads at {fact:q1.fraud_loss.App} of {fact:q1_total.fraud_loss}."], "action": None}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def rid(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    return client.post("/api/ask", json={"question": "Where?"}).json()["report_id"]


def test_vocabulary_route(client):
    r = client.get("/api/vocabulary")
    assert r.status_code == 200
    dims = {d["id"]: d["label"] for d in r.json()["dimensions"]}
    assert dims["orders.sales_channel"] == "Sales channel"


def section_url(rid, sid="s1"):
    return f"/api/reports/{rid}/sections/{sid}"


BY_REGION = {"metrics": ["fraud_loss"], "dimensions": ["dealers.region"]}


def test_query_route_reruns_and_reports_dropped_wording(client, rid):
    r = client.post(f"{section_url(rid)}/query", json=BY_REGION)
    assert r.status_code == 200
    assert r.json()["kind"] == "ok" and r.json()["wording_dropped"] is True
    body = client.get(f"/api/reports/{rid}").json()
    assert body["query_defs"]["q1"]["dimensions"] == ["dealers.region"]


def test_query_route_no_rows_is_422(client, rid):
    nope = {"metrics": ["fraud_loss"], "filters": [{"member": "orders.sales_channel", "op": "eq", "values": ["Nope"]}]}
    version = client.get(f"/api/reports/{rid}").json()
    r = client.post(f"{section_url(rid)}/query", json=nope)
    assert r.status_code == 422 and r.json()["detail"] == "No rows match Sales channel = Nope."
    assert client.get(f"/api/reports/{rid}").json() == version


def test_query_route_on_exploratory_is_409(client, fake_llm):
    fake_llm.push({"sql": "select 1 as one"})
    xid = client.post("/api/explore", json={"question": "x"}).json()["report_id"]
    r = client.post(f"{section_url(xid)}/query", json=BY_REGION)
    assert r.status_code == 409 and r.json()["detail"] == "An exploratory report has no query to edit."


def test_query_route_unknown_section_is_404(client, rid):
    assert client.post(f"{section_url(rid, 's9')}/query", json=BY_REGION).json()["detail"] == "No such section"
    assert client.post(f"{section_url('nope')}/query", json=BY_REGION).status_code == 404


# Task 5: chart forms and section commands.
def version(client, rid):
    return client.get(f"/api/reports/{rid}").json()


def test_report_payload_names_author_and_contributors(client, rid):
    body = version(client, rid)
    assert body["author"] == "ana" and body["contributors"] == ["ana"]


def test_forms_route(client, rid):
    assert client.get(f"{section_url(rid)}/forms").json() == {"forms": ["bars", "table", "segmented"]}


def test_chart_route_swaps_and_rejects_a_form_that_does_not_fit(client, rid):
    r = client.post(f"{section_url(rid)}/chart", json={"form": "line"})
    assert r.status_code == 422 and r.json()["detail"] == "line does not fit this query; choose one of: bars, table, segmented"
    r = client.post(f"{section_url(rid)}/chart", json={"form": "table"}, headers={"X-LR-User": "omar"})
    assert r.status_code == 200 and r.json()["kind"] == "ok"
    types = [b["type"] for b in version(client, rid)["spec"]["sections"][0]["blocks"]]
    assert "table" in types and "bars" not in types


def test_command_needs_editing_rights(client, fake_llm, rid):
    before = version(client, rid)
    r = client.post(f"{section_url(rid)}/command", json={"command": "shorter"}, headers={"X-LR-User": "omar"})
    assert r.status_code == 403 and r.json()["detail"] == "Only an author of this report can change its words."
    assert version(client, rid) == before
    fake_llm.push({"paragraphs": ["Total {fact:q1_total.fraud_loss}."]})
    r = client.post(f"{section_url(rid)}/command", json={"command": "shorter"}, headers={"X-LR-User": "ana"})
    assert r.status_code == 200 and r.json()["kind"] == "ok" and r.json()["cost"]["calls"] == 1


def test_remove_the_only_section_is_409(client, rid):
    r = client.post(f"{section_url(rid)}/command", json={"command": "remove"})
    assert r.status_code == 409 and r.json()["detail"] == "A report needs at least one section."


def test_rewrite_failure_reports_kind_failed(client, fake_llm, rid):
    before = version(client, rid)
    fake_llm.push({"paragraphs": ["Loss rose 38%."]}, {"paragraphs": ["Still 38%."]}, {"paragraphs": ["Now 38%."]})
    body = client.post(f"{section_url(rid)}/command", json={"command": "regenerate"}).json()
    assert body["kind"] == "failed" and body["message"].startswith("The model did not give a valid answer")
    assert body["cost"]["calls"] == 3  # narrate_attempts
    assert version(client, rid) == before


def test_command_rejects_an_unknown_command(client, rid):
    assert client.post(f"{section_url(rid)}/command", json={"command": "delete"}).status_code == 422


# Task 6: feedback per section.
def test_feedback_is_one_row_per_user_and_section(client, rid):
    from living_reports.store import get_store

    before = version(client, rid)
    url = f"{section_url(rid)}/feedback"
    assert client.post(url, json={"signal": "up"}).json() == {"ok": True}
    client.post(url, json={"signal": "down"}, headers={"X-LR-User": "ana"})
    client.post(url, json={"signal": "up"}, headers={"X-LR-User": "omar"})
    rows = get_store().rows("SELECT user, signal FROM feedback WHERE report_id = ? ORDER BY user", [rid])
    assert [tuple(r) for r in rows] == [("ana", "down"), ("omar", "up")]
    assert version(client, rid) == before
    assert client.get(url, headers={"X-LR-User": "omar"}).json() == {"signal": "up"}
    assert client.get(url, headers={"X-LR-User": "chidi"}).json() == {"signal": None}


def test_feedback_rejects_an_unknown_signal(client, rid):
    assert client.post(f"{section_url(rid)}/feedback", json={"signal": "meh"}).status_code == 422
    assert client.post(f"{section_url(rid, 's9')}/feedback", json={"signal": "up"}).status_code == 404


# Task 7: prose edits and block moves.
def test_prose_edit_rights_and_validation(client, fake_llm, rid):
    fake_llm.push({"claims": []})
    client.post(f"/api/reports/{rid}/editions")
    assert client.get(f"/api/reports/{rid}/publication").json()["draft_changed"] is False
    before = version(client, rid)
    url = f"{section_url(rid)}/prose"
    r = client.put(url, json={"paragraphs": ["Total {fact:q1_total.fraud_loss}."]}, headers={"X-LR-User": "omar"})
    assert r.status_code == 403
    r = client.put(url, json={"paragraphs": ["Loss rose 38%."]})
    assert r.status_code == 422 and r.json()["detail"].startswith("raw_number")
    assert r.json()["issues"][0]["code"] == "raw_number"
    r = client.put(url, json={"paragraphs": [""]})
    assert r.status_code == 422 and r.json()["detail"] == "A section needs at least one paragraph."
    assert version(client, rid) == before
    r = client.put(url, json={"paragraphs": ["Total {fact:q1_total.fraud_loss}."]})
    assert r.status_code == 200 and r.json()["kind"] == "ok"
    assert client.get(f"/api/reports/{rid}/publication").json()["draft_changed"] is True


def test_blocks_route_needs_rights_and_one_action(client, rid):
    url = f"{section_url(rid)}/blocks"
    assert client.post(url, json={"remove": 0}, headers={"X-LR-User": "omar"}).status_code == 403
    assert client.post(url, json={"order": [0], "remove": 0}).status_code == 422
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"order": [0, 0, 0, 0]}).json()["detail"] == "order must list every block once"
    n = len(version(client, rid)["spec"]["sections"][0]["blocks"])
    assert client.post(url, json={"order": list(reversed(range(n)))}).json()["kind"] == "ok"
    assert version(client, rid)["spec"]["sections"][0]["blocks"][0]["type"] == "bars"


# Final review fixes.
OMAR = {"X-LR-User": "omar"}


def test_a_chip_edit_gives_no_editing_rights(client, rid):
    assert client.post(f"{section_url(rid)}/query", json=BY_REGION, headers=OMAR).status_code == 200
    assert "omar" in version(client, rid)["contributors"]
    assert client.post(f"{section_url(rid)}/command", json={"command": "shorter"}, headers=OMAR).status_code == 403
    assert client.put(f"{section_url(rid)}/prose", json={"paragraphs": ["Words."]}, headers=OMAR).status_code == 403
    assert client.post(f"{section_url(rid)}/blocks", json={"remove": 0}, headers=OMAR).status_code == 403


def test_a_chart_swap_does_not_make_a_contributor(client, fake_llm, rid):
    client.post(f"{section_url(rid)}/chart", json={"form": "table"}, headers=OMAR)
    assert version(client, rid)["contributors"] == ["ana"]
    fake_llm.push({"claims": []})
    state = client.post(f"/api/reports/{rid}/editions").json()
    r = client.post(f"/api/reports/{rid}/editions/{state['number']}/sign",
                    json={"content_hash": state["content_hash"]}, headers=OMAR)
    assert r.status_code == 200


def test_a_thread_on_a_removed_section_is_closed(client, fake_llm, rid):
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"]}},
                  {**WORDS, "paragraphs": ["Orders: {fact:q2.order_count}."]})
    client.post("/api/ask", json={"question": "Orders?", "report_id": rid})
    thread = client.post(f"/api/reports/{rid}/threads", json={"section_id": "s1", "fact_id": "q1.fraud_loss.App"}).json()
    assert client.post(f"{section_url(rid)}/command", json={"command": "remove"}).status_code == 200
    threads = {t["id"]: t for t in client.get(f"/api/reports/{rid}/threads").json()}
    assert threads[thread["id"]]["status"] == "closed"
    r = client.post(f"/api/threads/{thread['id']}/messages", json={"text": "Why?"})
    assert r.status_code == 409


def test_a_thread_on_a_fact_that_vanishes_is_closed(client, rid):
    thread = client.post(f"/api/reports/{rid}/threads", json={"section_id": "s1", "fact_id": "q1.fraud_loss.App"}).json()
    client.post(f"{section_url(rid)}/query", json=BY_REGION)
    threads = {t["id"]: t for t in client.get(f"/api/reports/{rid}/threads").json()}
    assert threads[thread["id"]]["status"] == "closed"


def test_a_filter_change_marks_kept_words_stale(client, rid):
    east = {**ROUTED["query"], "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}]}
    body = client.post(f"{section_url(rid)}/query", json=east).json()
    assert body["wording_dropped"] is False and body["wording_stale"] is True
    same = client.post(f"{section_url(rid)}/query", json=east).json()
    assert same["wording_stale"] is False


def test_report_cost_accumulates(client, fake_llm, rid):
    before = version(client, rid)["cost"]
    client.post(f"{section_url(rid)}/chart", json={"form": "table"})
    assert version(client, rid)["cost"]["calls"] == before["calls"]
    fake_llm.push({"paragraphs": ["Total {fact:q1_total.fraud_loss}."]})
    client.post(f"{section_url(rid)}/command", json={"command": "shorter"})
    cost = version(client, rid)["cost"]
    assert cost["calls"] == before["calls"] + 1
    assert cost["input_tokens"] == before["input_tokens"] + 100


def test_query_route_bounds_its_lists(client, rid):
    many = {"metrics": ["fraud_loss"], "filters": [{"member": "orders.sales_channel", "op": "in", "values": [f"v{i}" for i in range(101)]}]}
    r = client.post(f"{section_url(rid)}/query", json=many)
    assert r.status_code == 422 and r.json()["detail"] == "A filter has at most 100 values."
    dims = {"metrics": ["fraud_loss"], "dimensions": [f"orders.d{i}" for i in range(6)]}
    assert client.post(f"{section_url(rid)}/query", json=dims).json()["detail"] == "A query has at most 5 dimensions."


def test_a_selection_range_must_be_in_order(client, fake_llm, rid):
    calls = len(fake_llm.prompts)
    r = client.post("/api/ask", json={"question": "Why?", "report_id": rid,
                                      "selection": {"filters": [], "range": ["2025-11-01", "2025-07-01"]}})
    assert r.status_code == 422 and len(fake_llm.prompts) == calls
