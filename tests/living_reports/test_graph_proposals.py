from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from folio_graph import NodeChange
from living_reports.api.app import create_app
from living_reports.engine import Query
from living_reports.engine.compile import compile
from living_reports.reports.editions import EditionStore
from living_reports.reports.store import ReportStore
from living_reports.store import get_store

from .conftest import friendly_payload

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}
OMAR, ANA = {"X-LR-User": "omar"}, {"X-LR-User": "ana"}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def new_payload(client):
    return friendly_payload(get_store())


def propose(client, who=ANA):
    return client.post("/api/graph/proposals", json={
        "node_id": "fraud_loss", "kind": "MetricBinding", "payload": new_payload(client),
        "reason": "Exclude friendly fraud."
    }, headers=who).json()


def _live_filter():
    return get_store().graph.slice().node("fraud_loss").payload["measure"]["filter"]


def test_proposal_is_a_candidate_and_changes_nothing(client):
    q = Query(metrics=["fraud_loss"])
    store = get_store()
    before = compile(q, store.graph.model()).hash
    p = propose(client)
    assert p["status"] == "pending" and compile(q, store.graph.model()).hash == before
    now = store.now()
    candidates = store.graph.graph.backend.nodes(now, now, ids=["fraud_loss"], statuses=("candidate",))
    assert [n.status for n in candidates] == ["candidate"]


def test_proposals_are_listed(client):
    p = propose(client)
    assert [x["id"] for x in client.get("/api/graph/proposals").json()] == [p["id"]]


def test_only_an_owner_confirms_a_metric(client):
    p = propose(client)
    assert client.post(f"/api/graph/proposals/{p['id']}/confirm", json={}, headers=ANA).status_code == 403


def test_confirm_changes_the_metric(client):
    p = propose(client)
    assert client.post(f"/api/graph/proposals/{p['id']}/confirm", json={}, headers=OMAR).status_code == 200
    assert _live_filter() is not None
    assert client.get("/api/graph/proposals").json() == []


def test_confirm_twice_is_rejected(client):
    p = propose(client)
    client.post(f"/api/graph/proposals/{p['id']}/confirm", json={}, headers=OMAR)
    assert client.post(f"/api/graph/proposals/{p['id']}/confirm", json={}, headers=OMAR).status_code == 409


def test_reject_leaves_the_metric_alone(client):
    p = propose(client)
    r = client.post(f"/api/graph/proposals/{p['id']}/reject", json={"reason": "No."}, headers=OMAR)
    assert r.status_code == 200
    assert _live_filter() is None
    assert get_store().graph.slice().node("fraud_loss").version == 1


def test_unknown_proposal_is_404(client):
    assert client.post("/api/graph/proposals/nope/confirm", json={}, headers=OMAR).status_code == 404


def test_replay_of_an_old_edition_gives_its_old_sql(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    rid = client.post("/api/ask", json={"question": "Where?"}).json()["report_id"]
    store = get_store()
    edition = EditionStore(store, ReportStore(store)).freeze(rid)
    p = propose(client)
    fake_llm.push({"claims": []})      # the approved change creates a new edition with checks
    client.post(f"/api/graph/proposals/{p['id']}/confirm", json={}, headers=OMAR)
    query = Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"])
    old = compile(query, store.graph.model(known_at=edition.known_at))
    assert old.sql == edition.queries["q1"].sql and "friendly" not in str(old.params)
    new = compile(query, store.graph.model())
    assert "FILTER" in new.sql and "friendly_fraud" in new.params


def test_a_later_valid_from_does_not_apply_early(store):
    future = datetime(2030, 1, 1)
    change = NodeChange(id="fraud_loss", kind="MetricBinding", payload=friendly_payload(store))
    proposal = store.graph.propose(change, "ana", "r")
    store.graph.confirm(proposal.id, "omar", valid_from=future)
    assert store.graph.model().metric("fraud_loss").measure.filter is None
    assert store.graph.model(valid_at=future).metric("fraud_loss").measure.filter is not None
