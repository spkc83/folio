"""The graph: proposals, confirmed versions, and reads by slice."""

import logging
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from folio_graph.backend import GraphBackend, Writer
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
from folio_graph.registry import Registry
from folio_graph.slice import GraphSlice

KIND = "Kind"
log = logging.getLogger(__name__)


class _KindDocument(BaseModel):
    spec: dict


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Graph:
    def __init__(self, backend: GraphBackend, clock: Callable[[], datetime] = utcnow):
        self.backend = backend
        self.clock = clock
        self.registry = Registry()
        self._projections: dict[str, Callable[[GraphSlice], Any]] = {}
        self._listeners: dict[str, list[Callable[[dict], None]]] = {}
        self.registry.register(
            KindSpec(KIND, _KindDocument, (EdgeRule("supersedes", ANY, ANY, "one"),))
        )

    # Kinds ---------------------------------------------------------------------------

    def register(self, spec: KindSpec, by: str = "system") -> None:
        """Register a kind, and record its schema as a ``Kind`` node when it changed."""
        self.registry.register(spec)
        payload = {"spec": self.registry.schema_document(spec)}
        now = self.clock()
        live = self.backend.open_version("nodes", spec.name)
        if live is not None and live.payload == payload:
            return
        with self.backend.transaction() as w:
            self._write(w, NodeChange(id=spec.name, kind=KIND, payload=payload), now, now)

    # Changes -------------------------------------------------------------------------

    def propose(self, change: NodeChange | EdgeChange, by: str, reason: str) -> Proposal:
        """Validate a change now and keep it as a candidate. A candidate changes no read."""
        now = self.clock()
        if isinstance(change, NodeChange):
            change = change.model_copy(update={"payload": self.registry.validate(change)})
            candidate: Node | Edge = Node(
                change.id, change.kind, change.payload, "candidate", 0, now, None, now, None
            )
        else:
            self._edge_rule(change, now)
            candidate = Edge(
                change.id,
                change.kind,
                change.from_id,
                change.to_id,
                change.role,
                change.payload,
                "candidate",
                0,
                now,
                None,
                now,
                None,
            )
        proposal = Proposal(
            id=uuid.uuid4().hex[:12],
            change=change,
            reason=reason,
            proposed_by=by,
            created_at=now,
        )
        with self.backend.transaction() as w:
            w.save_proposal(proposal)
            w.insert(candidate, proposal=proposal.id)
        return proposal

    def confirm(self, proposal_id: str, by: str, valid_from: datetime | None = None) -> Node | Edge:
        proposal = self._pending(proposal_id)
        now = self.clock()
        with self.backend.transaction() as w:
            entity = self._write(w, proposal.change, valid_from or now, now)
            w.close_candidate(proposal.id, now)
            confirmed = proposal.model_copy(
                update={"status": "confirmed", "decided_by": by, "decided_at": now}
            )
            w.save_proposal(confirmed)
        self.emit("confirm", {"proposal": confirmed, "entity": entity})
        return entity

    def reject(self, proposal_id: str, by: str, note: str) -> Proposal:
        proposal = self._pending(proposal_id)
        now = self.clock()
        closed = proposal.model_copy(
            update={"status": "rejected", "decided_by": by, "decided_at": now, "note": note}
        )
        with self.backend.transaction() as w:
            w.close_candidate(proposal.id, now)
            w.save_proposal(closed)
        return closed

    def retire(self, entity_id: str, by: str, valid_to: datetime | None = None) -> None:
        """End an entity's validity. Its history stays readable."""
        now = self.clock()
        table, live = self._open(entity_id)
        end = valid_to or now
        if end <= live.valid_from:
            raise GraphError(
                f"{entity_id} cannot end at {end}, before it started at {live.valid_from}"
            )
        with self.backend.transaction() as w:
            w.close_open(table, entity_id, now)
            w.insert(replace(live, valid_to=end, recorded_from=now, recorded_to=None))
        self.emit("retire", {"id": entity_id})

    def supersede(
        self,
        old_id: str,
        change: NodeChange,
        by: str,
        reason: str,
        valid_from: datetime | None = None,
    ) -> Node:
        """Confirm ``change`` as the successor of ``old_id``, link them, and end the old one."""
        if self.backend.open_version("nodes", old_id) is None:
            raise GraphError(f"{old_id} is not a live node")
        start = valid_from or self.clock()
        node = self.confirm(self.propose(change, by, reason).id, by, valid_from=start)
        link = EdgeChange(kind="supersedes", from_id=change.id, to_id=old_id)
        self.confirm(self.propose(link, by, reason).id, by, valid_from=start)
        self.retire(old_id, by, valid_to=start)
        return node

    # Reads ---------------------------------------------------------------------------

    def as_of(
        self, valid_at: datetime | None = None, known_at: datetime | None = None
    ) -> GraphSlice:
        now = self.clock()
        return GraphSlice(self.backend, valid_at or now, known_at or now)

    @staticmethod
    def diff(a: GraphSlice, b: GraphSlice) -> Diff:
        def versions(s: GraphSlice) -> dict[str, int]:
            return {e.id: e.version for e in [*s.nodes(), *s.edges()]}

        va, vb = versions(a), versions(b)
        return Diff(
            added=sorted(vb.keys() - va.keys()),
            removed=sorted(va.keys() - vb.keys()),
            changed=sorted((i, va[i], vb[i]) for i in va.keys() & vb.keys() if va[i] != vb[i]),
        )

    def register_projection(self, name: str, fn: Callable[[GraphSlice], Any]) -> None:
        self._projections[name] = fn

    def project(self, view: GraphSlice, name: str) -> Any:
        """Read a slice into a typed document. Consumers read projections, not raw nodes."""
        try:
            fn = self._projections[name]
        except KeyError:
            raise GraphError(f"Unknown projection: {name}") from None
        return fn(view)

    # Events --------------------------------------------------------------------------

    def on(self, event: str, fn: Callable[[dict], None]) -> None:
        self._listeners.setdefault(event, []).append(fn)

    def emit(self, event: str, payload: dict) -> None:
        """Run every listener. A failure does not undo the write; it is raised at the end."""
        errors = []
        for fn in self._listeners.get(event, []):
            try:
                fn(payload)
            except Exception as err:  # noqa: BLE001 - re-raised below
                log.exception("Listener for %s failed", event)
                errors.append(err)
        if errors:
            raise errors[0]

    # Internals -----------------------------------------------------------------------

    def _pending(self, proposal_id: str) -> Proposal:
        proposal = self.backend.proposal(proposal_id)
        if proposal is None:
            raise GraphError(f"Unknown proposal: {proposal_id}")
        if proposal.status != "pending":
            raise GraphError(f"Proposal {proposal_id} is already {proposal.status}")
        return proposal

    def _open(self, entity_id: str):
        for table in ("nodes", "edges"):
            live = self.backend.open_version(table, entity_id)
            if live is not None:
                return table, live
        raise GraphError(f"{entity_id} is not live")

    def _edge_rule(self, change: EdgeChange, now: datetime) -> EdgeRule:
        ends = {n.id: n for n in self.backend.nodes(now, now, ids=[change.from_id, change.to_id])}
        missing = [i for i in (change.from_id, change.to_id) if i not in ends]
        if missing:
            raise GraphError(f"Edge {change.id} needs live nodes: {', '.join(missing)}")
        return self.registry.edge_rule(
            change.kind, ends[change.from_id].kind, ends[change.to_id].kind
        )

    def _write(
        self, w: Writer, change: NodeChange | EdgeChange, start: datetime, now: datetime
    ) -> Node | Edge:
        """Write a confirmed version. Nothing is overwritten; old beliefs stay readable."""
        if isinstance(change, NodeChange):
            table = "nodes"
            fresh: Node | Edge = Node(
                change.id,
                change.kind,
                self.registry.validate(change),
                "confirmed",
                0,
                start,
                None,
                now,
                None,
            )
        else:
            table = "edges"
            rule = self._edge_rule(change, now)
            if rule.cardinality == "one":
                others = [
                    e
                    for e in self.backend.edges(now, now, kind=change.kind, from_id=change.from_id)
                    if e.id != change.id
                ]
                if others:
                    raise GraphError(
                        f"{change.from_id} can have at most one {change.kind} edge; "
                        f"it already has {others[0].id}"
                    )
            fresh = Edge(
                change.id,
                change.kind,
                change.from_id,
                change.to_id,
                change.role,
                change.payload,
                "confirmed",
                0,
                start,
                None,
                now,
                None,
            )
        live = self.backend.open_version(table, change.id)
        if live is not None and live.kind != change.kind:
            raise GraphError(
                f"{change.id} is a {live.kind}; a new version cannot make it a {change.kind}"
            )
        if live is not None and start < live.valid_from:
            raise GraphError(
                f"valid_from {start} is before the current version's {live.valid_from}"
            )
        version = self.backend.max_version(table, change.id) + 1
        if live is not None:
            w.close_open(table, change.id, now)
            if live.valid_from < start:
                w.insert(replace(live, valid_to=start, recorded_from=now, recorded_to=None))
        entity = replace(fresh, version=version)
        w.insert(entity)
        return entity
