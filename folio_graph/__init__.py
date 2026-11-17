"""A domain-free, bi-temporal property graph.

Kinds declare a payload schema and edge rules. Changes are proposed, then confirmed into
new versions; nothing is overwritten. Readers take a slice ``as_of(valid_at, known_at)``
and read it through projections.
"""

from folio_graph.backend import GraphBackend, SQLiteBackend
from folio_graph.engine import Graph
from folio_graph.model import (
    ANY,
    Diff,
    Edge,
    EdgeChange,
    EdgeRule,
    GraphError,
    KindSpec,
    Node,
    NodeChange,
    Proposal,
)
from folio_graph.slice import GraphSlice

__all__ = [
    "ANY",
    "Diff",
    "Graph",
    "GraphBackend",
    "GraphSlice",
    "SQLiteBackend",
    "Edge",
    "EdgeChange",
    "EdgeRule",
    "GraphError",
    "KindSpec",
    "Node",
    "NodeChange",
    "Proposal",
]
