"""A preview ``SemanticModel`` built from proposed changes, before anything is confirmed.

The probes compile against this model. It uses the projection's own rules, so a change
that the projection would refuse (a metric on a field that is not there) fails here too.
"""
from dataclasses import dataclass
from datetime import datetime

from folio_graph import Node, NodeChange
from folio_graph.engine import utcnow
from living_reports.semantic.model import SemanticModel
from living_reports.semantic.projections import semantic_model


@dataclass
class _PreviewSlice:
    """The part of a ``GraphSlice`` that the projection reads."""

    items: list[Node]
    valid_at: datetime
    known_at: datetime
    version: int = 0

    def nodes(self, kind: str | None = None) -> list[Node]:
        return [n for n in self.items if kind is None or n.kind == kind]


def _known_changes(known: SemanticModel) -> list[NodeChange]:
    """The payloads of a model's documents, as changes."""
    out = []
    if known.source:
        out.append(NodeChange(id=known.source, kind="Source",
                              payload={"name": known.source, "dialect": known.dialect}))
    for d in known.datasets:
        payload = d.model_dump(mode="json", exclude={"fields", "version"})
        out.append(NodeChange(id=d.name, kind="Dataset", payload=payload))
        for f in d.fields:
            payload = f.model_dump(mode="json", exclude={"id", "version"})
            out.append(NodeChange(id=f.id, kind="Field", payload=payload))
    for r in known.relationships:
        out.append(NodeChange(id=r.name, kind="Relationship",
                              payload=r.model_dump(mode="json", exclude={"version"})))
    for m in known.metrics:
        payload = {"name": m.id, **m.model_dump(mode="json", exclude={"id", "version"})}
        out.append(NodeChange(id=m.id, kind="MetricBinding", payload=payload))
    return out


def model_from_changes(
    changes: list[NodeChange], known: SemanticModel | None = None
) -> SemanticModel:
    """The model that ``changes`` would give over ``known``. Every version is 0.

    A change replaces the known entry with the same id. Raises ``GraphError`` when an
    entry refers to one that neither holds.
    """
    merged: dict[str, NodeChange] = {}
    for c in [*(_known_changes(known) if known else []), *changes]:
        merged[c.id] = c
    now = utcnow()
    nodes = [Node(c.id, c.kind, c.payload, "candidate", 0, now, None, now, None)
             for c in merged.values()]
    return semantic_model(_PreviewSlice(nodes, now, now))
