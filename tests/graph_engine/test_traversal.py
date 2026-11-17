from datetime import datetime

import pytest

from folio_graph import EdgeChange, NodeChange
from folio_graph.backend import SQLiteBackend
from folio_graph.engine import Graph

JOINS = [
    ("orders", "dealers"),
    ("dealers", "regions"),
    ("orders", "products"),
    ("regions", "orders"),
]


@pytest.fixture
def graph(clock, kinds) -> Graph:
    g = Graph(SQLiteBackend(), clock=clock)
    for spec in kinds:
        g.register(spec)
    for name in ["orders", "dealers", "regions", "products"]:
        p = g.propose(
            NodeChange(id=name, kind="Dataset", payload={"name": name, "primary_key": ["id"]}),
            "a",
            "x",
        )
        g.confirm(p.id, "o")
    for src, dst in JOINS:
        g.confirm(g.propose(EdgeChange(kind="joins_to", from_id=src, to_id=dst), "a", "x").id, "o")
    return g


def ids(path):
    return [e.id for e in path]


def test_paths_two_hops(graph):
    paths = graph.as_of().paths("orders", "regions", ["joins_to"])
    assert [ids(p) for p in paths] == [["orders|joins_to|dealers|", "dealers|joins_to|regions|"]]


def test_paths_respects_max_hops(graph):
    assert graph.as_of().paths("orders", "regions", ["joins_to"], max_hops=1) == []


def test_admissible_prunes(graph):
    paths = graph.as_of().paths(
        "orders", "regions", ["joins_to"], admissible=lambda e: e.to_id != "dealers"
    )
    assert paths == []


def test_cycle_terminates(graph):
    paths = graph.as_of().paths("orders", "orders", ["joins_to"], max_hops=3)
    assert [ids(p) for p in paths] == [
        [
            "orders|joins_to|dealers|",
            "dealers|joins_to|regions|",
            "regions|joins_to|orders|",
        ]
    ]


def test_paths_ordered_by_length(graph):
    graph.confirm(
        graph.propose(EdgeChange(kind="joins_to", from_id="orders", to_id="regions"), "a", "x").id,
        "o",
    )
    paths = graph.as_of().paths("orders", "regions", ["joins_to"])
    assert [len(p) for p in paths] == [1, 2]


def test_dependents_reverse_walk(graph):
    assert graph.as_of().dependents("regions") == ["dealers", "orders"]


def test_dependents_filters_edge_kinds(graph):
    assert graph.as_of().dependents("regions", ["uses"]) == []


def test_retired_edge_is_not_traversed(graph, clock):
    before = clock()
    clock.set(datetime(2024, 6, 1))
    graph.retire("orders|joins_to|dealers|", "o")
    assert graph.as_of().paths("orders", "regions", ["joins_to"]) == []
    old = graph.as_of(valid_at=before, known_at=datetime(2024, 6, 2))
    assert len(old.paths("orders", "regions", ["joins_to"])) == 1
