import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}
ANA, OMAR, CHIDI = ({"X-LR-User": u} for u in ("ana", "omar", "chidi"))


@pytest.fixture
def client(orders_file, monkeypatch):
    monkeypatch.setenv("LR_CHECKER_MODEL", "checker-model")
    monkeypatch.setenv("LR_SMALL_MODEL", "author-model")
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def rid(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    return client.post("/api/ask", json={"question": "Where?"}, headers=ANA).json()["report_id"]


def freeze(client, fake_llm, rid, claims=()):
    fake_llm.push({"claims": list(claims)})
    return client.post(f"/api/reports/{rid}/editions", headers=ANA).json()


def sign(client, rid, state, who):
    return client.post(f"/api/reports/{rid}/editions/{state['number']}/sign", json={"content_hash": state["content_hash"]}, headers=who)


def publish(client, rid, state, who=OMAR):
    return client.post(f"/api/reports/{rid}/editions/{state['number']}/publish", headers=who)


def set_tier(client, rid, tier, who=OMAR):
    return client.post(f"/api/reports/{rid}/tier", json={"tier": tier}, headers=who)


def test_team_report_needs_only_the_owner(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid)
    assert publish(client, rid, s).status_code == 409
    assert sign(client, rid, s, OMAR).status_code == 200
    r = publish(client, rid, s)
    assert r.status_code == 200 and client.get(f"/api/reports/{rid}/latest").json()["edition"] == 1


def test_author_cannot_sign(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid)
    r = sign(client, rid, s, ANA)
    assert r.status_code == 403 and "author" in r.json()["detail"].lower()


def test_owner_who_wrote_the_report_cannot_sign(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    rid = client.post("/api/ask", json={"question": "Where?"}, headers=OMAR).json()["report_id"]
    s = freeze(client, fake_llm, rid)
    assert sign(client, rid, s, OMAR).status_code == 403


def test_stale_hash_is_rejected_and_names_current_edition(client, fake_llm, rid):
    first = freeze(client, fake_llm, rid)
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"]}}, {**WORDS, "paragraphs": ["Orders: {fact:q2.order_count}."]})
    client.post("/api/ask", json={"question": "More", "report_id": rid}, headers=ANA)
    freeze(client, fake_llm, rid)
    r = sign(client, rid, first, OMAR)
    assert r.status_code == 409 and r.json()["current_edition"] == 2


def test_wrong_hash_on_current_edition_is_rejected(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid)
    assert sign(client, rid, {**s, "content_hash": "bad"}, OMAR).status_code == 409


def test_double_sign_is_idempotent(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid)
    sign(client, rid, s, OMAR)
    assert sign(client, rid, s, OMAR).status_code == 200
    state = client.get(f"/api/reports/{rid}/editions/1/state").json()
    assert len(state["signatures"]) == 1


def test_compliance_cannot_sign_before_owner(client, fake_llm, rid):
    set_tier(client, rid, "business_critical")
    s = freeze(client, fake_llm, rid)
    assert sign(client, rid, s, CHIDI).status_code == 409


def test_critical_report_needs_both_signatures(client, fake_llm, rid):
    set_tier(client, rid, "business_critical")
    s = freeze(client, fake_llm, rid)
    sign(client, rid, s, OMAR)
    assert publish(client, rid, s).status_code == 409
    assert sign(client, rid, s, CHIDI).status_code == 200
    assert publish(client, rid, s).status_code == 200


def test_compliance_signature_is_not_needed_on_team_reports(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid)
    sign(client, rid, s, OMAR)
    assert sign(client, rid, s, CHIDI).status_code == 409


def test_open_wording_blocks_a_critical_report(client, fake_llm, rid):
    set_tier(client, rid, "business_critical")
    s = freeze(client, fake_llm, rid, claims=[{"text": "drives", "issue": "cause"}])
    assert s["can_publish"] is False
    sign(client, rid, s, OMAR)
    sign(client, rid, s, CHIDI)
    assert publish(client, rid, s).status_code == 409


def test_compliance_can_resolve_wording_with_a_note(client, fake_llm, rid):
    set_tier(client, rid, "business_critical")
    s = freeze(client, fake_llm, rid, claims=[{"text": "drives", "issue": "cause"}])
    r = client.post(f"/api/reports/{rid}/editions/1/wording", json={"note": "The data supports this."}, headers=CHIDI)
    assert r.status_code == 200
    wording = next(c for c in r.json()["checks"] if c["step"] == "wording")
    assert wording["status"] == "pass" and "The data supports this." in str(wording["findings"])
    sign(client, rid, s, OMAR)
    sign(client, rid, s, CHIDI)
    assert publish(client, rid, s).status_code == 200


def test_only_compliance_resolves_wording(client, fake_llm, rid):
    freeze(client, fake_llm, rid, claims=[{"text": "drives", "issue": "cause"}])
    assert client.post(f"/api/reports/{rid}/editions/1/wording", json={"note": "fine"}, headers=OMAR).status_code == 403


def test_open_wording_does_not_block_a_team_report(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid, claims=[{"text": "drives", "issue": "cause"}])
    sign(client, rid, s, OMAR)
    assert publish(client, rid, s).status_code == 200


def test_failed_check_blocks_signing_and_publishing(client, fake_llm, rid):
    from living_reports.store import get_store
    from living_reports.reports.store import ReportStore
    conn = get_store()
    store = ReportStore(conn)
    report = store.get(rid)
    fact = report.facts["q1_total.fraud_loss"]
    report.facts[fact.id] = fact.model_copy(update={"formatted": "$9.9M"})
    store.save(report)
    s = freeze(client, fake_llm, rid)
    assert s["can_publish"] is False
    assert sign(client, rid, s, OMAR).status_code == 409


def test_new_edition_needs_new_signatures(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid)
    sign(client, rid, s, OMAR)
    publish(client, rid, s)
    fake_llm.push({**ROUTED, "query": {"metrics": ["order_count"]}}, {**WORDS, "paragraphs": ["Orders: {fact:q2.order_count}."]})
    client.post("/api/ask", json={"question": "More", "report_id": rid}, headers=ANA)
    second = freeze(client, fake_llm, rid)
    assert second["number"] == 2 and second["signatures"] == []
    assert publish(client, rid, second).status_code == 409


def test_wrong_role_is_rejected(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid)
    assert sign(client, rid, s, {"X-LR-User": "mallory"}).status_code == 403


def test_only_the_owner_publishes(client, fake_llm, rid):
    s = freeze(client, fake_llm, rid)
    sign(client, rid, s, OMAR)
    assert publish(client, rid, s, who=ANA).status_code == 403


def test_exploratory_report_cannot_be_published_or_promoted(client, fake_llm):
    fake_llm.push({"sql": "select 1 as x"})
    rid = client.post("/api/explore", json={"question": "x"}, headers=ANA).json()["report_id"]
    assert set_tier(client, rid, "team").status_code == 409
    s = freeze(client, fake_llm, rid)
    assert publish(client, rid, s).status_code == 409


def test_edition_state_lists_what_is_still_needed(client, fake_llm, rid):
    set_tier(client, rid, "business_critical")
    s = freeze(client, fake_llm, rid)
    assert s["needs"] == ["owner", "compliance"]
    sign(client, rid, s, OMAR)
    state = client.get(f"/api/reports/{rid}/editions/1/state").json()
    assert state["needs"] == ["compliance"]


def test_freezing_twice_does_not_run_the_checks_twice(client, fake_llm, rid):
    freeze(client, fake_llm, rid)
    r = client.post(f"/api/reports/{rid}/editions", headers=ANA)
    assert r.status_code == 200 and r.json()["number"] == 1
