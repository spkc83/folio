"""``FolioGraph``: the graph engine with Folio's kinds, derived edges, and projections.

Some edges follow from a node's payload (a Field ``belongs_to`` its Dataset). The graph
derives them when the node is confirmed and retires them when the node is retired or a
new version no longer names the target. Callers write the other edges (``Report uses``,
``Edition cites``) with ``link``.
"""

from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel

from folio_graph import (
    Edge,
    EdgeChange,
    Graph,
    GraphBackend,
    GraphError,
    GraphSlice,
    Node,
    NodeChange,
    Proposal,
)
from folio_graph.engine import utcnow
from living_reports.semantic.kinds import FOLIO_KINDS

DERIVED_EDGES = (
    "in_source",
    "belongs_to",
    "relates",
    "measures",
    "uses",
    "built_from",
    "discussed_in",
    "joins_to",
)

PROJECTIONS = ("semantic_model", "vocabulary")

# The edge kinds that each node kind derives from its own payload.
_DERIVED_BY_KIND: dict[str, tuple[str, ...]] = {
    "Dataset": ("in_source",),
    "Field": ("belongs_to",),
    "Relationship": ("relates",),
    "MetricBinding": ("measures", "uses"),
    "Edition": ("built_from",),
    "Thread": ("discussed_in",),
}


def wanted_edges(kind: str, node_id: str, payload: dict) -> list[EdgeChange]:
    """The derived edges a node of ``kind`` with ``payload`` should have."""

    def edge(edge_kind: str, to_id: str, from_id: str = node_id, **extra) -> EdgeChange:
        return EdgeChange(kind=edge_kind, from_id=from_id, to_id=to_id, **extra)

    if kind == "Dataset":
        return [edge("in_source", payload["source_id"])]
    if kind == "Field":
        return [edge("belongs_to", payload["dataset"])]
    if kind == "Relationship":
        src, dst = payload["from_dataset"], payload["to_dataset"]
        return [
            edge("relates", src),
            edge("relates", dst),
            edge(
                "joins_to",
                dst,
                from_id=src,
                role=payload.get("role"),
                payload={"relationship": payload["name"]},
            ),
        ]
    if kind == "MetricBinding":
        targets: list[str] = []
        if payload.get("measure"):
            m = payload["measure"]
            targets += [t for t in (m.get("field"), m.get("dataset")) if t]
            if m.get("filter"):
                targets.append(m["filter"]["member"])
        if payload.get("expression"):
            e = payload["expression"]
            targets += [*e["fields"], e["dataset"]]
        out = [edge("measures", t) for t in dict.fromkeys(targets)]
        if payload.get("ratio"):
            r = payload["ratio"]
            out += [edge("uses", t) for t in dict.fromkeys((r["numerator"], r["denominator"]))]
        return out
    if kind in ("Edition", "Thread"):
        edge_kind = "built_from" if kind == "Edition" else "discussed_in"
        return [edge(edge_kind, f"report:{payload['report_id']}")]
    return []


class FolioGraph:
    def __init__(self, backend: GraphBackend, clock: Callable[[], datetime] = utcnow):
        self.graph = Graph(backend, clock)
        for spec in FOLIO_KINDS:
            self.graph.register(spec)
        self._register_projections()
        self.graph.on("confirm", self._derive_edges)
        self.graph.on("retire", self._retire_derived)

    def _register_projections(self) -> None:
        def late(name: str) -> Callable[[GraphSlice], object]:
            # projections imports this module's types; import it when a read needs it.
            def run(view: GraphSlice) -> object:
                from living_reports.semantic import projections

                return getattr(projections, name)(view)

            return run

        for name in PROJECTIONS:
            self.graph.register_projection(name, late(name))

    @property
    def backend(self) -> GraphBackend:
        return self.graph.backend

    # Writes --------------------------------------------------------------------------

    def put(
        self,
        node_id: str,
        kind: str,
        payload: BaseModel | dict,
        *,
        by: str = "system",
        reason: str = "",
        valid_from: datetime | None = None,
    ) -> Node:
        """Propose and confirm a node in one call. A failure leaves nothing written."""
        if isinstance(payload, BaseModel):
            payload = payload.model_dump(mode="json")
        change = NodeChange(id=node_id, kind=kind, payload=payload)
        with self.backend.transaction():
            proposal = self.graph.propose(change, by, reason)
            return self.graph.confirm(proposal.id, by, valid_from=valid_from)

    def link(
        self,
        kind: str,
        from_id: str,
        to_id: str,
        *,
        role: str | None = None,
        payload: dict | None = None,
        by: str = "system",
        reason: str = "",
        valid_from: datetime | None = None,
    ) -> Edge:
        """Confirm an edge, or return the live one with the same id."""
        change = EdgeChange(
            kind=kind, from_id=from_id, to_id=to_id, role=role, payload=payload or {}
        )
        with self.backend.transaction():
            live = self.backend.open_version("edges", change.id)
            if live is not None:
                return live
            proposal = self.graph.propose(change, by, reason)
            return self.graph.confirm(proposal.id, by, valid_from=valid_from)

    def propose(self, change: NodeChange | EdgeChange, by: str, reason: str) -> Proposal:
        return self.graph.propose(change, by, reason)

    def confirm(
        self, proposal_id: str, by: str, valid_from: datetime | None = None
    ) -> Node | Edge:
        """Confirm a proposal and its derived edges in one transaction."""
        with self.backend.transaction():
            return self.graph.confirm(proposal_id, by, valid_from=valid_from)

    def reject(self, proposal_id: str, by: str, note: str) -> Proposal:
        return self.graph.reject(proposal_id, by, note)

    def proposal(self, proposal_id: str) -> Proposal | None:
        return self.backend.proposal(proposal_id)

    def pending(self) -> list[Proposal]:
        return self.backend.proposals("pending")

    # Reads ---------------------------------------------------------------------------

    def slice(self, valid_at: datetime | None = None, known_at: datetime | None = None) -> GraphSlice:
        return self.graph.as_of(valid_at, known_at)

    def version(self, known_at: datetime | None = None) -> int:
        return self.slice(known_at=known_at).version

    def model(self, valid_at: datetime | None = None, known_at: datetime | None = None):
        return self.graph.project(self.slice(valid_at, known_at), "semantic_model")

    def vocabulary(self, valid_at: datetime | None = None, known_at: datetime | None = None):
        return self.graph.project(self.slice(valid_at, known_at), "vocabulary")

    # Derived edges -------------------------------------------------------------------

    def _live_derived(self, node: Node, now: datetime) -> list[Edge]:
        kinds = _DERIVED_BY_KIND.get(node.kind, ())
        found = self.backend.edges(now, now, kind=list(kinds), from_id=node.id) if kinds else []
        if node.kind == "Relationship":
            found += [
                e
                for e in self.backend.edges(now, now, kind="joins_to")
                if e.payload.get("relationship") == node.id
            ]
        return found

    def _end_edge(self, edge: Edge, end: datetime, by: str) -> None:
        """Retire an edge at ``end``. An edge that starts at ``end`` never held: withdraw it."""
        if end > edge.valid_from:
            self.graph.retire(edge.id, by, valid_to=end)
            return
        with self.backend.transaction() as w:
            w.close_open("edges", edge.id, self.graph.clock())

    def _derive_edges(self, event: dict) -> None:
        node = event["entity"]
        if not isinstance(node, Node) or node.kind not in _DERIVED_BY_KIND:
            return
        by = event["proposal"].decided_by or "system"
        now = self.graph.clock()
        wanted = {e.id: e for e in wanted_edges(node.kind, node.id, node.payload)}
        ends = {i for e in wanted.values() for i in (e.from_id, e.to_id)}
        live_ids = {n.id for n in self.backend.nodes(now, now, ids=sorted(ends))}
        missing = sorted(ends - live_ids - {node.id})
        if missing:
            raise GraphError(
                f"{node.kind} {node.id} refers to {', '.join(missing)}, which is not in the graph"
            )
        live = {e.id: e for e in self._live_derived(node, now)}
        for edge_id, edge in live.items():
            if edge_id not in wanted:
                self._end_edge(edge, node.valid_from, by)
        for edge_id, change in wanted.items():
            if edge_id not in live:
                self.link(
                    change.kind,
                    change.from_id,
                    change.to_id,
                    role=change.role,
                    payload=change.payload,
                    by=by,
                    valid_from=node.valid_from,
                )

    def _retire_derived(self, event: dict) -> None:
        rows = [r for r in self.backend.history(event["id"]) if isinstance(r, Node)]
        if not rows:
            return
        node = rows[-1]
        now = self.graph.clock()
        end = node.valid_to or now
        for edge in self._live_derived(node, now):
            self._end_edge(edge, end, "system")
