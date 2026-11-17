from datetime import datetime

import pytest

from folio_graph import GraphError, NodeChange
from folio_graph.backend import SQLiteBackend
from folio_graph.engine import Graph

V1 = {"label": "Fraud loss", "definition": "Total loss.", "expr": {"agg": "sum"}}


@pytest.fixture
def graph(clock, kinds) -> Graph:
    g = Graph(SQLiteBackend(), clock=clock)
    for spec in kinds:
        g.register(spec)
    return g


def put(g, node_id, kind, payload):
    return g.confirm(
        g.propose(NodeChange(id=node_id, kind=kind, payload=payload), "a", "x").id, "o"
    )


def test_diff_added_removed_changed(graph, clock):
    put(graph, "fraud_loss", "Metric", V1)
    put(graph, "T", "Thread", {"title": "Old"})
    a = graph.as_of()
    clock.set(datetime(2024, 2, 1))
    put(graph, "fraud_loss", "Metric", {**V1, "label": "Loss"})
    graph.retire("T", "o")
    put(graph, "T2", "Thread", {"title": "New"})
    diff = graph.diff(a, graph.as_of())
    assert diff.added == ["T2"] and diff.removed == ["T"]
    assert diff.changed == [("fraud_loss", 1, 2)]


def test_projection_is_a_pure_read(graph, clock):
    graph.register_projection(
        "labels", lambda s: {n.id: n.payload["label"] for n in s.nodes("Metric")}
    )
    put(graph, "fraud_loss", "Metric", V1)
    old = graph.as_of()
    clock.set(datetime(2024, 2, 1))
    put(graph, "fraud_loss", "Metric", {**V1, "label": "Loss"})
    assert graph.project(old, "labels") == {"fraud_loss": "Fraud loss"}
    assert graph.project(graph.as_of(), "labels") == {"fraud_loss": "Loss"}


def test_unknown_projection(graph):
    with pytest.raises(GraphError, match="Unknown projection: nope"):
        graph.project(graph.as_of(), "nope")


def test_confirm_event_fires_after_commit(graph):
    seen = []
    graph.on("confirm", lambda e: seen.append(graph.as_of().node(e["entity"].id).version))
    put(graph, "fraud_loss", "Metric", V1)
    assert seen == [1]


def test_retire_event(graph):
    seen = []
    graph.on("retire", lambda e: seen.append(e["id"]))
    put(graph, "T", "Thread", {"title": "Old"})
    graph.retire("T", "o", valid_to=datetime(2024, 3, 1))
    assert seen == ["T"]


def test_failing_listener_keeps_the_write_and_runs_the_rest(graph):
    seen = []

    def boom(event):
        raise RuntimeError("listener failed")

    graph.on("confirm", boom)
    graph.on("confirm", lambda e: seen.append(e["entity"].id))
    with pytest.raises(RuntimeError, match="listener failed"):
        put(graph, "fraud_loss", "Metric", V1)
    assert seen == ["fraud_loss"]
    assert graph.as_of().node("fraud_loss").version == 1
