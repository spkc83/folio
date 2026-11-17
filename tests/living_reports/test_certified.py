"""The certified question set: written on publish, answered at zero model cost while current."""
import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.reports.certified import list_certified, question_id
from living_reports.reports.editions import EditionStore
from living_reports.reports.store import ReportStore
from living_reports.store import get_store

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
REGION = {**ROUTED, "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]}}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}
RWORDS = {**WORDS, "title": "Orders by region", "paragraphs": ["Orders: {fact:q1_total.order_count}."]}
ANA, OMAR, CHIDI = ({"X-LR-User": u} for u in ("ana", "omar", "chidi"))
STALE = "This certified question no longer matches the model."


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def ask(client, fake_llm, routed=ROUTED, words=WORDS, question="Where?"):
    fake_llm.push(routed, words)
    return client.post("/api/ask", json={"question": question}, headers=ANA).json()["report_id"]


def publish(client, fake_llm, rid, signers=(OMAR,)):
    fake_llm.push({"claims": []})
    state = client.post(f"/api/reports/{rid}/editions", headers=ANA).json()
    if CHIDI in signers:
        client.post(f"/api/reports/{rid}/editions/{state['number']}/wording",
                    json={"note": "Reviewed."}, headers=CHIDI)
    for who in signers:
        r = client.post(f"/api/reports/{rid}/editions/{state['number']}/sign",
                        json={"content_hash": state["content_hash"]}, headers=who)
        assert r.status_code == 200, r.text
    r = client.post(f"/api/reports/{rid}/editions/{state['number']}/publish", headers=OMAR)
    assert r.status_code == 200, r.text
    return state["number"]


def confirm_definition_change(client, fake_llm, reports=1):
    fake_llm.push(*[{"claims": []}] * reports)
    items = client.post("/api/sources/duckdb_demo/advance").json()
    item = next(c for c in items if c["kind"] == "definition")
    fake_llm.push(*[{"claims": []}] * reports)
    r = client.post(f"/api/graph/proposals/{item['proposal_id']}/confirm", json={}, headers=OMAR)
    assert r.status_code == 200, r.text


def test_publish_certifies_each_section(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    store = get_store()
    edition = EditionStore(store, ReportStore(store)).get(rid, 1)
    [cq] = list_certified(store)
    assert cq.question == "Where?" and cq.report_id == rid and cq.section_id == "s1"
    assert cq.query["metrics"] == ["fraud_loss"] and cq.query["dimensions"] == ["orders.sales_channel"]
    assert cq.verified_by == "omar" and cq.graph_version == edition.graph_version
    assert cq.expected == {"q1_total.fraud_loss": edition.facts["q1_total.fraud_loss"].formatted}
    assert cq.current is True and cq.id == question_id("Where?", cq.query)
    [listed] = client.get("/api/certified").json()
    assert listed["id"] == cq.id and listed["verified_at"].startswith(str(cq.verified_at.date()))


def test_republish_replaces_not_duplicates(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    first = list_certified(get_store())[0].verified_at
    client.post(f"/api/reports/{rid}/tier", json={"tier": "business_critical"}, headers=OMAR)
    assert publish(client, fake_llm, rid, signers=(OMAR, CHIDI)) == 2
    [cq] = list_certified(get_store())
    assert cq.verified_at > first


def test_exploratory_is_never_certified(client, fake_llm):
    fake_llm.push({"sql": "select 1 as x"})
    rid = client.post("/api/explore", json={"question": "x"}).json()["report_id"]
    fake_llm.push({"claims": []})
    client.post(f"/api/reports/{rid}/editions", headers=ANA)
    assert client.post(f"/api/reports/{rid}/editions/1/publish", headers=OMAR).status_code == 409
    assert list_certified(get_store()) == []


def test_certified_ask_is_zero_cost_and_opens_the_report(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    [cq] = list_certified(get_store())
    calls = len(fake_llm.prompts)
    r = client.post("/api/ask", json={"certified_id": cq.id}).json()
    assert r["kind"] == "report" and r["report_id"] == rid and r["view"] == "narrative"
    assert r["edition"] == 1
    assert r["cost"]["calls"] == 0 and r["cost"]["rung"] == "certified"
    assert len(fake_llm.prompts) == calls


def test_an_unrelated_answer_does_not_unverify(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    version = get_store().graph.version()
    ask(client, fake_llm, REGION, RWORDS, question="Orders?")
    assert get_store().graph.version() > version
    assert list_certified(get_store())[0].current is True


def test_an_unrelated_definition_does_not_unverify(client, fake_llm):
    rid = ask(client, fake_llm, REGION, RWORDS, question="Orders?")
    publish(client, fake_llm, rid)
    confirm_definition_change(client, fake_llm)
    assert list_certified(get_store())[0].current is True


def test_certified_after_a_definition_change_is_reanswered_and_not_verified(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    confirm_definition_change(client, fake_llm)
    [cq] = list_certified(get_store())
    assert cq.current is False
    fake_llm.push(WORDS)
    r = client.post("/api/ask", json={"certified_id": cq.id}).json()
    assert r["kind"] == "report" and r["report_id"] != rid and r["cost"]["calls"] == 1
    assert client.get(f"/api/reports/{r['report_id']}").json()["verified"] is None
    # The live report was refreshed, so it no longer shows what was certified.
    assert client.get(f"/api/reports/{rid}").json()["verified"] is None
    verified = client.get(f"/api/reports/{rid}/editions/1").json()["verified"]
    assert verified["by"] == "omar" and verified["current"] is False and verified["at"]


def test_certified_with_a_stale_query_is_skipped_and_fails_cleanly(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    store = get_store()
    [cq] = list_certified(store)
    store.execute("UPDATE certified_questions SET query = ?", ['{"metrics": []}'])
    assert list_certified(store) == []
    assert client.get("/api/certified").json() == []
    r = client.post("/api/ask", json={"certified_id": cq.id}).json()
    assert r["kind"] == "failed" and r["message"] == STALE


def test_unknown_certified_id_is_404(client):
    r = client.post("/api/ask", json={"certified_id": "nope"})
    assert r.status_code == 404 and r.json()["detail"] == "No such certified question"


def test_ask_needs_a_question_or_an_id(client):
    assert client.post("/api/ask", json={}).status_code == 422
    assert client.post("/api/ask", json={"question": "   "}).status_code == 422


def test_payload_carries_verified(client, fake_llm):
    rid = ask(client, fake_llm)
    assert client.get(f"/api/reports/{rid}").json()["verified"] is None
    publish(client, fake_llm, rid)
    for path in (f"/api/reports/{rid}", f"/api/reports/{rid}/editions/1", f"/api/reports/{rid}/latest"):
        verified = client.get(path).json()["verified"]
        assert verified["by"] == "omar" and verified["current"] is True


def test_question_id_normalizes_the_question():
    query = {"metrics": ["fraud_loss"]}
    assert question_id("  Where  is\nloss? ", query) == question_id("where is loss?", query)
    assert question_id("where is loss?", query) != question_id("where is loss?", {"metrics": ["order_count"]})
    assert len(question_id("x", query)) == 16


def test_certified_answer_is_the_published_edition(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    [cq] = list_certified(get_store())
    assert cq.edition == 1 and cq.content_hash == EditionStore(get_store(), ReportStore(get_store())).get(rid, 1).content_hash
    r = client.post("/api/ask", json={"certified_id": cq.id}).json()
    assert r["report_id"] == rid and r["edition"] == 1 and r["cost"]["rung"] == "certified"


def test_a_draft_change_after_publish_is_not_served_as_certified(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    [cq] = list_certified(get_store())
    fake_llm.push(REGION, {**RWORDS, "paragraphs": ["Orders: {fact:q2_total.order_count}."]})
    follow = client.post("/api/ask", json={"question": "And orders?", "report_id": rid}, headers=ANA).json()
    assert follow["report_id"] == rid
    assert len(client.get(f"/api/reports/{rid}").json()["spec"]["sections"]) == 2
    assert client.get(f"/api/reports/{rid}").json()["verified"] is None
    calls = len(fake_llm.prompts)
    r = client.post("/api/ask", json={"certified_id": cq.id}).json()
    assert r["edition"] == 1 and r["cost"]["calls"] == 0 and len(fake_llm.prompts) == calls
    edition = client.get(f"/api/reports/{rid}/editions/1").json()
    assert len(edition["spec"]["sections"]) == 1 and edition["verified"]["by"] == "omar"


def test_unpublished_edition_shows_no_verified_badge(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    client.post(f"/api/reports/{rid}/tier", json={"tier": "business_critical"}, headers=OMAR)
    fake_llm.push({"claims": []})
    assert client.post(f"/api/reports/{rid}/editions", headers=ANA).json()["number"] == 2
    assert client.get(f"/api/reports/{rid}/editions/2").json()["verified"] is None
    assert client.get(f"/api/reports/{rid}/editions/1").json()["verified"]["by"] == "omar"
    assert client.get(f"/api/reports/{rid}/latest").json()["verified"]["by"] == "omar"
    assert client.get(f"/api/reports/{rid}").json()["verified"] is None
    assert publish(client, fake_llm, rid, signers=(OMAR, CHIDI)) == 2
    # A published edition's certification is history: edition 1 keeps its badge.
    assert client.get(f"/api/reports/{rid}/editions/1").json()["verified"]["by"] == "omar"
    assert client.get(f"/api/reports/{rid}/editions/2").json()["verified"]["by"] == "omar"
    assert client.get(f"/api/reports/{rid}").json()["verified"]["by"] == "omar"


def test_a_missing_certified_edition_is_answered_again(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    [cq] = list_certified(get_store())
    get_store().execute("UPDATE certified_questions SET content_hash = 'other'")
    fake_llm.push(WORDS)
    r = client.post("/api/ask", json={"certified_id": cq.id}).json()
    assert r["kind"] == "report" and r["report_id"] != rid and r["cost"]["calls"] == 1
    assert client.get(f"/api/reports/{rid}/editions/1").json()["verified"] is None


ORDERS_FOLLOW = {**RWORDS, "paragraphs": ["Orders: {fact:q2_total.order_count}."]}


def test_edition_badge_is_not_current_when_any_section_changed(client, fake_llm):
    rid = ask(client, fake_llm)
    # "How many orders?" hashes below "Where?", so its row sorts first under ORDER BY id.
    fake_llm.push(REGION, ORDERS_FOLLOW)
    client.post("/api/ask", json={"question": "How many orders?", "report_id": rid}, headers=ANA)
    publish(client, fake_llm, rid)
    rows = get_store().rows("SELECT section_id FROM certified_questions WHERE report_id = ?", [rid])
    assert sorted(r[0] for r in rows) == ["s1", "s2"]
    assert client.get(f"/api/reports/{rid}/editions/1").json()["verified"]["current"] is True
    # Only section 1 (fraud_loss) reads the changed definition; section 2 does not.
    confirm_definition_change(client, fake_llm)
    current = {cq.section_id: cq.current for cq in list_certified(get_store())}
    assert current == {"s1": False, "s2": True}
    assert client.get(f"/api/reports/{rid}/editions/1").json()["verified"]["current"] is False


def test_a_retired_metric_question_is_skipped_and_fails_cleanly(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    [cq] = list_certified(get_store())
    get_store().graph.graph.retire("fraud_loss", by="omar")
    assert list_certified(get_store()) == []
    assert client.get("/api/digest").json()["suggested_questions"] == []
    r = client.post("/api/ask", json={"certified_id": cq.id}).json()
    assert r["kind"] == "failed" and r["message"] == STALE and not r["can_explore"]


def open_question_cards(client):
    return [c for c in client.get("/api/changes").json() if c["kind"] == "question" and c["status"] == "open"]


def test_certified_change_cards_do_not_pile_up(client, fake_llm):
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    confirm_definition_change(client, fake_llm)
    [card] = open_question_cards(client)
    latest = client.get(f"/api/reports/{rid}").json()["facts"]["q1_total.fraud_loss"]["formatted"]
    assert card["detail"].split(". Verified")[0].endswith(f"→ {latest}")
    publish(client, fake_llm, rid)
    assert open_question_cards(client) == []
    [cq] = list_certified(get_store())
    assert cq.current is True and cq.expected["q1_total.fraud_loss"] == latest


def test_another_report_with_the_same_question_keeps_the_first_badge(client, fake_llm):
    first = ask(client, fake_llm)
    publish(client, fake_llm, first)
    # The choice path skips the answer cache, so the same question makes a second report.
    fake_llm.push(WORDS)
    second = client.post("/api/ask", json={"question": "Where?", "choice": ROUTED["query"]},
                         headers=ANA).json()["report_id"]
    assert second != first
    publish(client, fake_llm, second)
    [cq] = list_certified(get_store())
    assert cq.report_id == second
    assert client.get(f"/api/reports/{first}/editions/1").json()["verified"]["by"] == "omar"
    assert client.get(f"/api/reports/{second}/editions/1").json()["verified"]["by"] == "omar"
    r = client.post("/api/ask", json={"certified_id": cq.id}).json()
    assert r["report_id"] == second and r["edition"] == 1


def test_a_synonym_change_does_not_unverify(client, fake_llm):
    from living_reports.semantic.kinds import MetricBindingPayload
    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    fg = get_store().graph
    payload = dict(fg.slice().node("fraud_loss").payload)
    payload["synonyms"] = ["losses"]
    payload["label"] = "Fraud losses"
    fg.put("fraud_loss", "MetricBinding", MetricBindingPayload.model_validate(payload), by="omar")
    assert fg.slice().node("fraud_loss").version == 2
    assert list_certified(get_store())[0].current is True


def test_store_migrates_an_old_certified_table(orders_file, fake_llm, tmp_path):
    import sqlite3
    data = tmp_path / "lr"
    data.mkdir(exist_ok=True)
    old = sqlite3.connect(data / "app.sqlite")
    old.execute(
        "CREATE TABLE certified_questions (id TEXT PRIMARY KEY, question TEXT NOT NULL, "
        "query TEXT NOT NULL, report_id TEXT NOT NULL, section_id TEXT NOT NULL, "
        "graph_version INTEGER NOT NULL, definitions TEXT NOT NULL, expected TEXT NOT NULL, "
        "verified_by TEXT NOT NULL, verified_at TEXT NOT NULL)"
    )
    old.execute("INSERT INTO certified_questions VALUES ('x', 'q', '{}', 'r', 's1', 1, 'd', '{}', 'omar', 't')")
    old.commit()
    old.close()
    with TestClient(create_app()) as c:
        rid = ask(c, fake_llm)
        publish(c, fake_llm, rid)
        assert [cq.report_id for cq in list_certified(get_store())] == [rid]


def _change_relationship(store):
    from living_reports.semantic.kinds import RelationshipPayload

    payload = RelationshipPayload.model_validate(store.graph.slice().node("orders_dealer").payload)
    store.graph.put("orders_dealer", "Relationship", payload.model_copy(update={"role": "seller"}))


def test_a_relationship_change_unverifies_only_questions_that_join_through_it(client, fake_llm):
    joined = ask(client, fake_llm, routed=REGION, words=RWORDS, question="Orders by region?")
    publish(client, fake_llm, joined)
    single = ask(client, fake_llm)
    publish(client, fake_llm, single)
    store = get_store()
    assert all(cq.current for cq in list_certified(store))
    _change_relationship(store)
    current = {cq.report_id: cq.current for cq in list_certified(store)}
    assert current == {joined: False, single: True}


def test_definitions_hash_covers_the_joined_relationship(store):
    from living_reports.reports.certified import definitions_hash

    by_region = {"metrics": ["order_count"], "dimensions": ["dealers.region"]}
    by_channel = {"metrics": ["order_count"], "dimensions": ["orders.sales_channel"]}
    before = (definitions_hash(by_region, store.graph.model()),
              definitions_hash(by_channel, store.graph.model()))
    _change_relationship(store)
    after = (definitions_hash(by_region, store.graph.model()),
             definitions_hash(by_channel, store.graph.model()))
    assert after[0] != before[0] and after[1] == before[1]


def test_a_stored_pre_engine_query_is_upgraded_on_read(client, fake_llm):
    import json

    rid = ask(client, fake_llm)
    publish(client, fake_llm, rid)
    store = get_store()
    old = {"metrics": ["fraud_loss"], "dimensions": ["sales_channel"], "filters": [],
           "start": None, "end": None, "top": None}
    store.execute("UPDATE certified_questions SET query = ?", [json.dumps(old)])
    [cq] = list_certified(store)
    assert cq.query["dimensions"] == ["orders.sales_channel"] and "top" not in cq.query
    assert cq.current is True
