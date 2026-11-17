import pytest

from folio_graph import GraphError, NodeChange
from folio_graph.registry import Registry

LOSS = {"label": "Fraud loss", "definition": "Total loss.", "expr": {"agg": "sum"}}


@pytest.fixture
def registry(kinds) -> Registry:
    reg = Registry()
    for spec in kinds:
        reg.register(spec)
    return reg


def test_valid_payload_is_normalized(registry):
    change = NodeChange(id="fraud_loss", kind="Metric", payload=LOSS)
    assert registry.validate(change) == LOSS


def test_bad_payload_names_kind_and_field(registry):
    bad = {k: v for k, v in LOSS.items() if k != "label"}
    with pytest.raises(GraphError) as err:
        registry.validate(NodeChange(id="fraud_loss", kind="Metric", payload=bad))
    assert "Metric" in str(err.value) and "label" in str(err.value)


def test_unknown_kind(registry):
    with pytest.raises(GraphError, match="Unknown kind: Nope"):
        registry.kind("Nope")


def test_edge_rule_lookup_and_rejection(registry):
    assert registry.edge_rule("decided_in", "Decision", "Thread").cardinality == "one"
    with pytest.raises(GraphError, match="No rule allows Metric -\\[decided_in\\]-> Thread"):
        registry.edge_rule("decided_in", "Metric", "Thread")


def test_wildcard_rule_matches_any_kind(registry):
    from folio_graph import EdgeRule, KindSpec
    from tests.graph_engine.conftest import Thread

    registry.register(KindSpec("Note", Thread, (EdgeRule("supersedes", "*", "*", "one"),)))
    assert registry.edge_rule("supersedes", "Metric", "Metric").cardinality == "one"


def test_schema_document_has_schema_and_edges(registry):
    doc = registry.schema_document(registry.kind("Decision"))
    assert doc["schema"]["required"] == ["text", "approved_by"]
    assert doc["edges"] == [
        {
            "edge_kind": "decided_in",
            "from_kind": "Decision",
            "to_kind": "Thread",
            "cardinality": "one",
        }
    ]
