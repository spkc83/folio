"""The definition change from the redesign proposal, section 4.1, with generic kinds."""

from datetime import datetime

from folio_graph import EdgeChange, NodeChange
from folio_graph.backend import SQLiteBackend
from folio_graph.engine import Graph

V1 = {
    "label": "Fraud loss",
    "definition": "Total loss on orders confirmed as fraud.",
    "expr": {"agg": "sum", "field": "orders.loss_amount"},
}
V2 = {
    "label": "Fraud loss",
    "definition": "Total loss on orders confirmed as fraud, excluding friendly fraud.",
    "expr": {
        "agg": "sum",
        "field": "orders.loss_amount",
        "filter": {"member": "orders.fraud_type", "op": "ne", "values": ["friendly_fraud"]},
    },
}


def test_worked_example(clock, kinds):
    g = Graph(SQLiteBackend(), clock=clock)
    for spec in kinds:
        g.register(spec)

    def put(node_id, kind, payload, valid_from=None):
        p = g.propose(NodeChange(id=node_id, kind=kind, payload=payload), "ana", "demo")
        return g.confirm(p.id, "omar", valid_from=valid_from)

    def link(kind, src, dst):
        g.confirm(
            g.propose(EdgeChange(kind=kind, from_id=src, to_id=dst), "ana", "demo").id, "omar"
        )

    put("fraud_loss", "Metric", V1)
    put("R", "Report", {"title": "Where is fraud loss concentrated?"})
    link("uses", "R", "fraud_loss")

    clock.set(datetime(2025, 7, 20))
    put("T", "Thread", {"title": "Should friendly fraud count as loss?"})
    put(
        "D",
        "Decision",
        {
            "text": "Exclude friendly fraud from fraud loss, effective 2025-07-01.",
            "approved_by": "Omar",
        },
    )
    link("decided_in", "D", "T")
    link("discussed_in", "T", "R")
    put("fraud_loss", "Metric", V2, valid_from=datetime(2025, 7, 1))
    link("derived_from", "fraud_loss", "D")

    edition_1 = g.as_of(valid_at=datetime(2025, 7, 10), known_at=datetime(2025, 7, 10))
    edition_2 = g.as_of(known_at=datetime(2025, 7, 21), valid_at=datetime(2025, 7, 21))
    june_rule = g.as_of(valid_at=datetime(2025, 6, 15), known_at=datetime(2025, 7, 21))

    assert edition_1.node("fraud_loss").version == 1
    assert edition_2.node("fraud_loss").version == 2
    assert june_rule.node("fraud_loss").version == 1

    walk = edition_2.paths("fraud_loss", "T", ["derived_from", "decided_in"])
    assert [[e.to_id for e in p] for p in walk] == [["D", "T"]]
    assert edition_2.node("D").payload["approved_by"] == "Omar"

    diff = g.diff(edition_1, edition_2)
    assert ("fraud_loss", 1, 2) in diff.changed
    assert {"D", "T"} <= set(diff.added)
    assert "R" in edition_2.dependents("fraud_loss")
    assert "R" in edition_2.dependents("D") and "R" not in edition_1.dependents("D")
