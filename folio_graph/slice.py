"""A consistent read of the graph: what was true at ``valid_at``, as known at ``known_at``."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from folio_graph.backend import GraphBackend
from folio_graph.model import Edge, Node


@dataclass(frozen=True)
class GraphSlice:
    backend: GraphBackend
    valid_at: datetime
    known_at: datetime

    @property
    def version(self) -> int:
        """The graph's version stamp as known at ``known_at``."""
        return self.backend.version_stamp(self.known_at)

    def node(self, node_id: str) -> Node | None:
        found = self.backend.nodes(self.valid_at, self.known_at, ids=[node_id])
        return found[0] if found else None

    def nodes(self, kind: str | None = None) -> list[Node]:
        return self.backend.nodes(self.valid_at, self.known_at, kind=kind)

    def edges(
        self, kind: str | None = None, from_id: str | None = None, to_id: str | None = None
    ) -> list[Edge]:
        return self.backend.edges(
            self.valid_at, self.known_at, kind=kind, from_id=from_id, to_id=to_id
        )

    def dependents(self, node_id: str, edge_kinds: list[str] | None = None) -> list[str]:
        """Ids that reach ``node_id`` along edges in this slice, at any distance."""
        found = self.backend.reachable(
            node_id, edge_kinds, "in", self.valid_at, self.known_at, max_hops=32
        )
        return [i for i, _ in found]

    def paths(
        self,
        from_id: str,
        to_id: str,
        edge_kinds: list[str],
        max_hops: int = 2,
        admissible: Callable[[Edge], bool] = lambda e: True,
    ) -> list[list[Edge]]:
        """Every simple path along edge direction, shortest first.

        The graph enumerates and the caller judges: ``admissible`` decides each step.
        """
        out: dict[str, list[Edge]] = {}
        for edge in self.backend.edges(self.valid_at, self.known_at, kind=edge_kinds):
            if admissible(edge):
                out.setdefault(edge.from_id, []).append(edge)
        found: list[list[Edge]] = []

        def walk(at: str, path: list[Edge], seen: set[str]) -> None:
            for edge in out.get(at, []):
                if edge.to_id == to_id:
                    found.append([*path, edge])
                elif edge.to_id not in seen and len(path) + 1 < max_hops:
                    walk(edge.to_id, [*path, edge], seen | {edge.to_id})

        if max_hops > 0:
            walk(from_id, [], {from_id})
        return sorted(found, key=lambda p: (len(p), [e.id for e in p]))
