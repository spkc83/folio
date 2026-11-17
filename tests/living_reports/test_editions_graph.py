from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from folio_graph import GraphError
from living_reports.api.app import create_app
from living_reports.reports.editions import EditionStore, reproduce
from living_reports.reports.store import ReportStore
from living_reports.semantic.graph import FolioGraph
from living_reports.store import get_store

from .test_definition_change import confirm, setup_report

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


def editions() -> EditionStore:
    store = get_store()
    return EditionStore(store, ReportStore(store))


def test_edition_records_graph_version_and_known_at(rid):
    store = get_store()
    before = store.graph.version()
    edition = editions().freeze(rid)
    assert edition.graph_version == before > 0
    assert store.graph.version(known_at=edition.known_at) >= before
    assert isinstance(edition.known_at, datetime)
    s = store.graph.slice()
    node = s.node(f"edition:{rid}:1")
    assert node is not None and node.payload["graph_version"] == edition.graph_version
    assert [e.to_id for e in s.edges(kind="built_from", from_id=node.id)] == [f"report:{rid}"]
    assert [e.to_id for e in s.edges(kind="cites", from_id=node.id)] == ["fraud_loss"]


def test_same_content_writes_no_second_node(rid):
    store = get_store()
    editions().freeze(rid)
    before = store.graph.version()
    assert editions().freeze(rid).number == 1
    assert store.graph.version() == before


def test_edition_reproduces_from_graph(client, fake_llm):
    rid, item = setup_report(client, fake_llm)
    store = get_store()
    first = editions().get(rid, 1)
    assert confirm(client, fake_llm, item).status_code == 200
    again = reproduce(first, store)
    assert again["q1"].sql == first.queries["q1"].sql
    assert again["q1"].plan.metric_versions == {"fraud_loss": 1}
    newest = editions().newest(rid)
    assert newest.number > 1 and newest.graph_version > first.graph_version
    assert reproduce(newest, store)["q1"].plan.metric_versions == {"fraud_loss": 2}
    assert store.graph.slice(known_at=first.known_at).node("fraud_loss").version == 1


def test_freeze_is_atomic_with_its_graph_node(rid, monkeypatch):
    def broken(self, *args, **kwargs):
        raise GraphError("graph write failed")

    monkeypatch.setattr(FolioGraph, "put", broken)
    with pytest.raises(GraphError):
        editions().freeze(rid)
    assert editions().newest(rid) is None


def test_diff_carries_graph_versions(client, fake_llm):
    rid, item = setup_report(client, fake_llm)
    confirm(client, fake_llm, item)
    newest = editions().newest(rid).number
    body = client.get(f"/api/reports/{rid}/diff?from=1&to={newest}").json()
    v1, v2 = body["graph_versions"]
    assert v1 < v2


def test_report_node_uses_members(rid):
    edges = get_store().graph.slice().edges(kind="uses", from_id=f"report:{rid}")
    assert {e.to_id for e in edges} == {"fraud_loss", "orders.sales_channel"}
