from datetime import datetime

import pytest
from pydantic import BaseModel

from folio_graph import EdgeChange, GraphError, KindSpec, NodeChange
from folio_graph.backend import SQLiteBackend
from folio_graph.engine import Graph

V1 = {"label": "Fraud loss", "definition": "Total loss.", "expr": {"agg": "sum"}}
V2 = {**V1, "definition": "Total loss, excluding friendly fraud."}


@pytest.fixture
def graph(clock, kinds) -> Graph:
    g = Graph(SQLiteBackend(), clock=clock)
    for spec in kinds:
        g.register(spec)
    return g


def put(g: Graph, node_id: str, kind: str, payload: dict, valid_from=None):
    proposal = g.propose(NodeChange(id=node_id, kind=kind, payload=payload), "ana", "test")
    return g.confirm(proposal.id, "omar", valid_from=valid_from)


def link(g: Graph, kind: str, src: str, dst: str):
    proposal = g.propose(EdgeChange(kind=kind, from_id=src, to_id=dst), "ana", "test")
    return g.confirm(proposal.id, "omar")


def test_first_confirm_writes_version_1(graph, clock):
    node = put(graph, "fraud_loss", "Metric", V1)
    assert node.version == 1
    assert node.valid_from == node.recorded_from == datetime(2024, 1, 1)


def test_later_rule_bounds_the_earlier_one(graph, clock):
    put(graph, "fraud_loss", "Metric", V1)
    clock.set(datetime(2025, 7, 20))
    put(graph, "fraud_loss", "Metric", V2, valid_from=datetime(2025, 7, 1))
    read = lambda v, k: graph.as_of(valid_at=v, known_at=k).node("fraud_loss").version
    assert read(datetime(2025, 6, 15), datetime(2025, 7, 21)) == 1
    assert read(datetime(2025, 7, 21), datetime(2025, 7, 21)) == 2
    assert read(datetime(2025, 7, 21), datetime(2025, 7, 10)) == 1


def test_same_valid_from_is_a_correction(graph, clock):
    put(graph, "fraud_loss", "Metric", V1)
    clock.set(datetime(2024, 2, 1))
    node = put(graph, "fraud_loss", "Metric", V2, valid_from=datetime(2024, 1, 1))
    assert node.version == 2
    before = graph.as_of(valid_at=datetime(2024, 3, 1), known_at=datetime(2024, 1, 15))
    assert before.node("fraud_loss").version == 1
    assert graph.as_of().node("fraud_loss").payload == V2


def test_backdated_confirm_is_refused(graph, clock):
    put(graph, "fraud_loss", "Metric", V1)
    clock.set(datetime(2025, 7, 20))
    put(graph, "fraud_loss", "Metric", V2, valid_from=datetime(2025, 7, 1))
    clock.set(datetime(2025, 8, 1))
    proposal = graph.propose(NodeChange(id="fraud_loss", kind="Metric", payload=V1), "ana", "x")
    rows = graph.backend.history("fraud_loss")
    with pytest.raises(GraphError) as err:
        graph.confirm(proposal.id, "omar", valid_from=datetime(2025, 1, 1))
    assert "2025-01-01" in str(err.value) and "2025-07-01" in str(err.value)
    assert graph.backend.history("fraud_loss") == rows
    assert graph.backend.proposal(proposal.id).status == "pending"


def test_two_proposals_never_give_two_versions(graph, clock):
    a = graph.propose(NodeChange(id="fraud_loss", kind="Metric", payload=V1), "ana", "a")
    b = graph.propose(NodeChange(id="fraud_loss", kind="Metric", payload=V2), "ana", "b")
    t0 = clock()
    clock.set(datetime(2024, 2, 1))
    graph.confirm(b.id, "omar")
    clock.set(datetime(2024, 3, 1))
    graph.confirm(a.id, "omar")
    for known in [t0, datetime(2024, 2, 1), datetime(2024, 3, 1), datetime(2024, 4, 1)]:
        ids = [
            n.id for n in graph.as_of(valid_at=datetime(2024, 4, 1), known_at=known).nodes("Metric")
        ]
        assert len(ids) == len(set(ids)) <= 1
    assert graph.as_of().node("fraud_loss").payload == V1


def test_candidate_is_invisible_until_confirmed(graph):
    proposal = graph.propose(NodeChange(id="fraud_loss", kind="Metric", payload=V1), "ana", "x")
    assert graph.as_of().node("fraud_loss") is None
    graph.confirm(proposal.id, "omar")
    assert graph.as_of().node("fraud_loss").status == "confirmed"


def test_reject_closes_candidate_and_blocks_confirm(graph):
    proposal = graph.propose(NodeChange(id="fraud_loss", kind="Metric", payload=V1), "ana", "x")
    rejected = graph.reject(proposal.id, "omar", "Not now.")
    assert rejected.status == "rejected" and rejected.note == "Not now."
    assert graph.backend.nodes(graph.clock(), graph.clock(), statuses=("candidate",)) == []
    with pytest.raises(GraphError, match="already rejected"):
        graph.confirm(proposal.id, "omar")


def test_bad_payload_is_refused_at_proposal(graph):
    with pytest.raises(GraphError, match="Metric"):
        graph.propose(NodeChange(id="x", kind="Metric", payload={"label": "X"}), "ana", "x")


def test_edge_rules_enforced(graph):
    put(graph, "fraud_loss", "Metric", V1)
    put(graph, "T", "Thread", {"title": "Friendly fraud"})
    put(graph, "T2", "Thread", {"title": "Other"})
    put(graph, "D", "Decision", {"text": "Exclude.", "approved_by": "Omar"})
    with pytest.raises(GraphError, match="No rule allows"):
        graph.propose(EdgeChange(kind="decided_in", from_id="fraud_loss", to_id="T"), "ana", "x")
    link(graph, "decided_in", "D", "T")
    with pytest.raises(GraphError, match="at most one"):
        link(graph, "decided_in", "D", "T2")


def test_edge_needs_live_endpoints(graph):
    put(graph, "D", "Decision", {"text": "Exclude.", "approved_by": "Omar"})
    with pytest.raises(GraphError, match="T"):
        graph.propose(EdgeChange(kind="decided_in", from_id="D", to_id="T"), "ana", "x")


def test_kind_reregistered_with_new_schema_versions_kind_node(graph, clock):
    put(graph, "fraud_loss", "Metric", V1)
    assert graph.as_of().node("Metric").version == 1

    class MetricV2(BaseModel):
        label: str
        definition: str
        expr: dict
        unit: str | None = None

    clock.set(datetime(2024, 2, 1))
    graph.register(KindSpec("Metric", MetricV2))
    assert graph.as_of().node("Metric").version == 2
    assert graph.as_of().node("fraud_loss").payload == V1
    graph.register(KindSpec("Metric", MetricV2))
    assert graph.as_of().node("Metric").version == 2


def test_file_backend_survives_reopen(tmp_path, clock, kinds):
    g = Graph(SQLiteBackend(tmp_path / "g.db"), clock=clock)
    for spec in kinds:
        g.register(spec)
    put(g, "fraud_loss", "Metric", V1)
    again = Graph(SQLiteBackend(tmp_path / "g.db"), clock=clock)
    assert again.as_of().node("fraud_loss").payload == V1


def test_supersede_links_and_ends_the_old_node(graph, clock):
    put(graph, "fraud_loss", "Metric", V1)
    clock.set(datetime(2025, 7, 20))
    new = NodeChange(id="net_fraud_loss", kind="Metric", payload=V2)
    graph.supersede("fraud_loss", new, "omar", "Renamed.", valid_from=datetime(2025, 7, 1))
    now = graph.as_of()
    assert now.node("fraud_loss") is None and now.node("net_fraud_loss").version == 1
    assert [e.to_id for e in now.edges(kind="supersedes", from_id="net_fraud_loss")] == [
        "fraud_loss"
    ]
    assert graph.as_of(valid_at=datetime(2025, 6, 1)).node("fraud_loss").version == 1


def test_supersede_needs_a_live_node(graph):
    with pytest.raises(GraphError, match="not a live node"):
        graph.supersede("nope", NodeChange(id="x", kind="Metric", payload=V1), "omar", "x")


def test_kind_change_under_one_id_is_refused(graph):
    put(graph, "orders", "Dataset", {"name": "orders", "primary_key": ["id"]})
    proposal = graph.propose(NodeChange(id="orders", kind="Thread", payload={"title": "t"}), "a", "x")
    with pytest.raises(GraphError, match="orders is a Dataset"):
        graph.confirm(proposal.id, "o")
    assert graph.as_of().node("orders").kind == "Dataset"
