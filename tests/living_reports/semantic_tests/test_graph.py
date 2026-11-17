from datetime import datetime

import pytest

from folio_graph import GraphError
from living_reports.engine.expressions import parse_expression
from living_reports.semantic.kinds import (
    FieldPayload,
    MetricBindingPayload,
    RelationshipPayload,
    ReportPayload,
)

from .conftest import dealers_dataset, field, fraud_loss, orders_dataset, source


@pytest.fixture
def seeded(fg):
    fg.put("duckdb_demo", "Source", source())
    fg.put("orders", "Dataset", orders_dataset())
    fg.put("dealers", "Dataset", dealers_dataset())
    fg.put("orders.loss_amount", "Field", field("loss_amount", "decimal"))
    fg.put("orders.dealer_id", "Field", field("dealer_id"))
    fg.put("orders.fraud_type", "Field", field("fraud_type", dimension={}))
    fg.put("dealers.dealer_id", "Field", field("dealer_id", dataset="dealers"))
    fg.put("dealers.region", "Field", field("region", dimension={}, dataset="dealers"))
    return fg


def _relationship() -> RelationshipPayload:
    return RelationshipPayload(
        name="orders_dealer",
        from_dataset="orders",
        to_dataset="dealers",
        from_columns=["dealer_id"],
        to_columns=["dealer_id"],
    )


def test_put_confirms_and_derives_edges(seeded):
    edges = seeded.slice().edges(kind="in_source", from_id="orders")
    assert [e.to_id for e in edges] == ["duckdb_demo"]
    belongs = seeded.slice().edges(kind="belongs_to", from_id="orders.loss_amount")
    assert [e.to_id for e in belongs] == ["orders"]


def test_relationship_writes_joins_to(seeded):
    seeded.put("orders_dealer", "Relationship", _relationship())
    paths = seeded.slice().paths("orders", "dealers", ["joins_to"])
    assert len(paths) == 1
    (edge,) = paths[0]
    assert edge.payload == {"relationship": "orders_dealer"}
    assert edge.role is None
    relates = seeded.slice().edges(kind="relates", from_id="orders_dealer")
    assert sorted(e.to_id for e in relates) == ["dealers", "orders"]


def test_relationship_needs_matching_columns():
    with pytest.raises(ValueError):
        RelationshipPayload(
            name="x", from_dataset="a", to_dataset="b", from_columns=["a"], to_columns=["a", "b"]
        )


def test_metric_measures_its_field_and_dependents_reach_it(seeded):
    seeded.put("fraud_loss", "MetricBinding", fraud_loss())
    s = seeded.slice()
    assert [e.to_id for e in s.edges(kind="measures", from_id="fraud_loss")] == [
        "orders.loss_amount"
    ]
    assert "fraud_loss" in s.dependents("orders.loss_amount")
    on_orders = s.dependents("orders")
    assert "orders.loss_amount" in on_orders
    assert "fraud_loss" in on_orders


def test_expression_metric_measures_every_field(seeded):
    payload = MetricBindingPayload(
        name="odd_loss",
        label="Odd loss",
        format="currency",
        expression=parse_expression(
            "sum(CASE WHEN orders.fraud_type <> 'none' THEN orders.loss_amount END)",
            "orders",
            seeded.model(),
        ),
    )
    seeded.put("odd_loss", "MetricBinding", payload)
    targets = {e.to_id for e in seeded.slice().edges(kind="measures", from_id="odd_loss")}
    assert targets == {"orders.loss_amount", "orders.fraud_type", "orders"}


def test_new_version_moves_derived_edges(seeded, clock):
    seeded.put("orders.order_value", "Field", field("order_value", "decimal"))
    seeded.put("fraud_loss", "MetricBinding", fraud_loss())
    (old,) = seeded.slice().edges(kind="measures", from_id="fraud_loss")
    clock.set(datetime(2024, 2, 1))
    seeded.put("fraud_loss", "MetricBinding", fraud_loss(field="orders.order_value"))
    live = seeded.slice().edges(kind="measures", from_id="fraud_loss")
    assert [e.to_id for e in live] == ["orders.order_value"]
    assert any(
        getattr(row, "valid_to", None) is not None
        for row in seeded.graph.backend.history(old.id)
    )


def test_retired_relationship_removes_join(seeded, clock):
    seeded.put("orders_dealer", "Relationship", _relationship())
    clock.set(datetime(2024, 2, 1))
    seeded.graph.retire("orders_dealer", "omar")
    assert seeded.slice().paths("orders", "dealers", ["joins_to"]) == []


def test_missing_endpoint_is_a_clear_error(seeded):
    with pytest.raises(GraphError, match="ghost"):
        seeded.put(
            "orders.nope",
            "Field",
            FieldPayload(name="nope", dataset="ghost", column="nope", type="string"),
        )


def test_link_is_idempotent(seeded):
    seeded.put("fraud_loss", "MetricBinding", fraud_loss())
    seeded.put("report:r1", "Report", ReportPayload(title="R", tier="exploratory", author="ana"))
    first = seeded.link("uses", "report:r1", "fraud_loss")
    second = seeded.link("uses", "report:r1", "fraud_loss")
    assert first.id == second.id
    assert len(seeded.slice().edges(kind="uses", from_id="report:r1")) == 1


def test_version_stamp_exposed(seeded):
    before = seeded.version()
    seeded.put("fraud_loss", "MetricBinding", fraud_loss())
    assert seeded.version() > before


def test_filter_member_is_a_measured_dependency(seeded):
    from living_reports.semantic.kinds import FilterSpec

    flt = FilterSpec(member="orders.fraud_type", op="ne", values=["friendly_fraud"])
    seeded.put("fraud_loss", "MetricBinding", fraud_loss(filter=flt))
    assert "fraud_loss" in seeded.slice().dependents("orders.fraud_type")
