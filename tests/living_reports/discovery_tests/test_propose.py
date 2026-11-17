import pytest

from living_reports.discovery.draft import DraftMetric, DraftRelationship
from living_reports.discovery.propose import Dropped, draft_model, to_changes
from living_reports.semantic.kinds import SourcePayload


@pytest.fixture
def registry():
    from folio_graph import SQLiteBackend
    from living_reports.semantic.graph import FolioGraph

    return FolioGraph(SQLiteBackend()).graph.registry


def _change(changes, node_id):
    return next(c for c in changes if c.id == node_id)


def test_draft_model_uses_structured_output(fake_llm, catalog, draft):
    fake_llm.push(draft.model_dump())
    assert draft_model(catalog, [], "small", "t1") == draft
    schema = fake_llm.schemas[-1]
    assert schema["properties"]["datasets"]["items"]["properties"]["name"]["enum"] == [
        "dealers", "orders"]
    assert "<catalog>" in fake_llm.prompts[-1]


def test_changes_are_in_confirmation_order(catalog, draft, registry):
    changes, dropped = to_changes(draft, catalog, SourcePayload(name="test"))
    kinds = [c.kind for c in changes]
    n_fields = 7 + 2  # orders without blob_col, dealers
    assert kinds == ["Source", "Dataset", "Dataset", *["Field"] * n_fields, "Relationship",
                     *["MetricBinding"] * 4]
    assert [c.id for c in changes if c.kind == "Dataset"] == ["dealers", "orders"]
    orders = _change(changes, "orders").payload
    assert orders["time_field"] == "order_created_date"
    assert orders["primary_key"] == ["order_id"] and orders["table"] == "main.orders"
    assert _change(changes, "orders.order_created_date").payload["dimension"] == {"is_time": True}
    assert _change(changes, "orders.sales_channel").payload["dimension"] == {"is_time": False}
    assert _change(changes, "orders.loss_amount").payload["dimension"] is None
    assert _change(changes, "orders.loss_amount").payload["label"] == "Loss amount"
    fraud_loss = _change(changes, "fraud_loss").payload
    assert fraud_loss["measure"] == {"agg": "sum", "field": "orders.loss_amount",
                                     "dataset": None, "filter": None}
    assert fraud_loss["certified"] is True and fraud_loss["synonyms"] == ["loss"]
    for change in changes:
        assert registry.validate(change) == change.payload
    assert dropped == [Dropped(name="orders.blob_col", kind="Field", reason="unsupported type BLOB")]


def test_unmapped_columns_are_dropped_not_proposed(catalog, draft):
    changes, dropped = to_changes(draft, catalog, SourcePayload(name="test"))
    assert all(c.id != "orders.blob_col" for c in changes)
    assert Dropped(name="orders.blob_col", kind="Field", reason="unsupported type BLOB") in dropped


def test_a_dataset_the_draft_omits_is_not_proposed(catalog, draft):
    draft.datasets = [d for d in draft.datasets if d.name == "orders"]
    draft.relationships = []
    draft.metrics = [m for m in draft.metrics if m.name != "dealer_count"]
    changes, dropped = to_changes(draft, catalog, SourcePayload(name="test"))
    assert not any(c.id.startswith("dealers") for c in changes)
    assert [d.name for d in dropped] == ["orders.blob_col"]


def test_bad_relationship_is_dropped(catalog, draft):
    draft.relationships = [
        DraftRelationship(name="to_ghost", from_dataset="orders", to_dataset="ghost",
                          from_columns=["dealer_id"], to_columns=["dealer_id"], confidence=0.5),
        DraftRelationship(name="uneven", from_dataset="orders", to_dataset="dealers",
                          from_columns=["dealer_id", "region"], to_columns=["dealer_id"],
                          confidence=0.5),
        DraftRelationship(name="no_column", from_dataset="orders", to_dataset="dealers",
                          from_columns=["nope"], to_columns=["dealer_id"], confidence=0.5),
    ]
    changes, dropped = to_changes(draft, catalog, SourcePayload(name="test"))
    reasons = {d.name: d.reason for d in dropped}
    assert reasons["to_ghost"] == "unknown dataset ghost"
    assert "differ in length" in reasons["uneven"]
    assert reasons["no_column"] == "unknown column orders.nope"
    assert not any(c.kind == "Relationship" for c in changes)


def test_metric_id_collision_is_dropped(catalog, draft):
    draft.metrics.append(DraftMetric(name="orders", label="Orders", format="count", agg="count",
                                     dataset="orders"))
    changes, dropped = to_changes(draft, catalog, SourcePayload(name="test"))
    assert Dropped(name="orders", kind="MetricBinding", reason="name collides with Dataset") in dropped
    assert [c.kind for c in changes if c.id == "orders"] == ["Dataset"]


def test_metric_on_an_unknown_field_is_dropped(catalog, draft):
    draft.metrics.append(DraftMetric(name="ghost_sum", label="Ghost", format="number", agg="sum",
                                     field="orders.ghost"))
    _, dropped = to_changes(draft, catalog, SourcePayload(name="test"))
    assert Dropped(name="ghost_sum", kind="MetricBinding",
                   reason="unknown field orders.ghost") in dropped
