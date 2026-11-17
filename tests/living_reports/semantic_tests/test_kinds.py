import pytest
from pydantic import ValidationError

from folio_graph import SQLiteBackend
from living_reports.semantic.graph import FolioGraph, wanted_edges
from living_reports.semantic.kinds import (
    FilterSpec,
    Measure,
    MetricBindingPayload,
    Ratio,
)

KINDS = (
    "Source",
    "Dataset",
    "Field",
    "Relationship",
    "MetricBinding",
    "Report",
    "Edition",
    "Thread",
)


def test_metric_binding_needs_exactly_one_form():
    with pytest.raises(ValidationError):
        MetricBindingPayload(name="x", label="X", format="count")
    with pytest.raises(ValidationError):
        MetricBindingPayload(
            name="x",
            label="X",
            format="count",
            measure=Measure(agg="count", dataset="orders"),
            ratio=Ratio(numerator="a", denominator="b"),
        )


def test_measure_count_needs_a_dataset_or_field():
    with pytest.raises(ValidationError):
        Measure(agg="count")
    assert Measure(agg="count", dataset="orders").dataset == "orders"
    with pytest.raises(ValidationError):
        Measure(agg="sum")


def test_filter_spec_value_counts():
    with pytest.raises(ValidationError):
        FilterSpec(member="orders.fraud_type", op="eq", values=[])
    with pytest.raises(ValidationError):
        FilterSpec(member="orders.order_value", op="between", values=[1])
    assert FilterSpec(member="orders.fraud_type", op="is_null", values=[]).values == []
    assert FilterSpec(member="orders.fraud_type", op="in", values=["a", "b"]).op == "in"
    with pytest.raises(ValidationError):
        FilterSpec(member="orders.fraud_type", op="in", values=[])
    with pytest.raises(ValidationError):
        FilterSpec(member="orders.fraud_type", op="is_null", values=["x"])


def test_sql_strings_are_not_payload_fields():
    with pytest.raises(ValidationError) as err:
        MetricBindingPayload.model_validate(
            {"name": "fraud_loss", "label": "Fraud loss", "format": "currency",
             "expr": "sum(loss_amount)"}
        )
    assert "expr" in str(err.value)


def test_every_kind_registers():
    fg = FolioGraph(SQLiteBackend())
    for name in KINDS:
        assert fg.graph.registry.kind(name).name == name
    assert fg.graph.registry.edge_rule("joins_to", "Dataset", "Dataset").cardinality == "many"
    assert fg.graph.registry.edge_rule("built_from", "Edition", "Report").cardinality == "one"


def test_wanted_edges_needs_no_graph():
    payload = MetricBindingPayload(
        name="fraud_rate_ratio",
        label="Rate",
        format="percent",
        ratio=Ratio(numerator="fraud_loss", denominator="order_value"),
    ).model_dump(mode="json")
    edges = wanted_edges("MetricBinding", "fraud_rate_ratio", payload)
    assert {(e.kind, e.to_id) for e in edges} == {
        ("uses", "fraud_loss"),
        ("uses", "order_value"),
    }


@pytest.mark.parametrize(
    "model, data",
    [
        ("FieldPayload", {"name": "x", "dataset": "orders", "column": 'a" OR 1=1', "type": "string"}),
        ("FieldPayload", {"name": "x y", "dataset": "orders", "column": "x", "type": "string"}),
        ("DatasetPayload", {"name": "orders", "source_id": "s", "table": "main.orders; drop",
                            "primary_key": ["id"]}),
        ("DatasetPayload", {"name": "orders", "source_id": "s", "table": "t",
                            "primary_key": ["id)"]}),
        ("Measure", {"agg": "sum", "field": "orders.loss_amount) --"}),
        ("FilterSpec", {"member": "orders.x' or 1", "op": "eq", "values": ["a"]}),
        ("RelationshipPayload", {"name": "r", "from_dataset": "a", "to_dataset": "b",
                                 "from_columns": ["k;"], "to_columns": ["k"]}),
        ("Ratio", {"numerator": "a b", "denominator": "c"}),
    ],
)
def test_identifiers_are_validated(model, data):
    from living_reports.semantic import kinds

    with pytest.raises(ValidationError):
        getattr(kinds, model).model_validate(data)


def test_valid_identifiers_pass():
    from living_reports.semantic.kinds import DatasetPayload

    d = DatasetPayload(name="orders", source_id="duckdb_demo", table="main.orders",
                       primary_key=["order_id"])
    assert d.table == "main.orders"
    assert Measure(agg="sum", field="orders.loss_amount").field == "orders.loss_amount"


def test_a_dataset_may_have_no_declared_key():
    from living_reports.semantic.kinds import DatasetPayload

    p = DatasetPayload(name="transactions", source_id="card_fraud", table="main.transactions")
    assert p.primary_key == []
