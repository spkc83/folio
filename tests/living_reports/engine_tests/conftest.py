"""The core engine fixture: two small tables and a model built directly (no graph)."""

from datetime import datetime

import duckdb
import pytest

from living_reports.semantic.kinds import (
    DimensionSpec,
    FilterSpec,
    Measure,
    MetricBindingPayload,
    Ratio,
    SqlExpression,
)
from living_reports.semantic.model import Dataset, Field, Metric, Relationship, SemanticModel

T0 = datetime(2024, 1, 1)

ORDERS = [
    ("O1", "2025-01-15", "App", "D1", 100, 1, "identity_theft", 500),
    ("O2", "2025-02-15", "App", "D2", 50, 1, "friendly_fraud", 300),
    ("O3", "2025-03-15", "Web", "D1", 30, 1, "identity_theft", 200),
    ("O4", "2025-04-15", "Web", "D3", 70, 1, "identity_theft", 400),
    ("O5", "2024-06-15", "App", "D1", 20, 1, "identity_theft", 100),
    ("O6", "2025-05-15", "App", "D1", 0, 0, "none", 250),
    ("O7", "2025-06-15", "Web", "D2", 0, 0, "none", 150),
    ("O8", "2025-07-15", "Telesales", "D3", 40, 1, "friendly_fraud", 350),
]
DEALERS = [("D1", "East"), ("D2", "East"), ("D3", "West"), ("D4", "West")]


def make_field(dataset, name, type="string", dimension=None, expression=None, column=None, label=None):
    return Field(
        id=f"{dataset}.{name}",
        name=name,
        dataset=dataset,
        column=column or name,
        type=type,
        label=label or name.replace("_", " ").capitalize(),
        dimension=dimension,
        expression=expression,
        version=1,
    )


def make_metric(metric_id, fmt="number", **form):
    return Metric(id=metric_id, label=metric_id.replace("_", " ").capitalize(), format=fmt,
                  version=1, **form)


def build_model() -> SemanticModel:
    orders_fields = [
        make_field("orders", "order_id"),
        make_field("orders", "order_created_date", "date", DimensionSpec(is_time=True)),
        make_field("orders", "sales_channel", dimension=DimensionSpec()),
        make_field("orders", "dealer_id"),
        make_field("orders", "loss_amount", "decimal"),
        make_field("orders", "is_fraud", "integer"),
        make_field("orders", "fraud_type", dimension=DimensionSpec()),
        make_field("orders", "order_value", "decimal"),
        make_field("orders", "loss_k", "decimal",
                   expression=SqlExpression(sql="loss_amount / 1000")),
    ]
    dealers_fields = [
        make_field("dealers", "dealer_id"),
        make_field("dealers", "region", dimension=DimensionSpec()),
    ]
    datasets = [
        Dataset(name="orders", source_id="duckdb_demo", table="main.orders",
                primary_key=["order_id"], time_field="order_created_date",
                fields=orders_fields, version=1),
        Dataset(name="dealers", source_id="duckdb_demo", table="main.dealers",
                primary_key=["dealer_id"], fields=dealers_fields, version=1),
    ]
    rel = Relationship(name="orders_dealer", from_dataset="orders", to_dataset="dealers",
                       from_columns=["dealer_id"], to_columns=["dealer_id"], version=1)
    metrics = [
        make_metric("fraud_loss", "currency", measure=Measure(agg="sum", field="orders.loss_amount")),
        make_metric("net_fraud_loss", "currency", measure=Measure(
            agg="sum", field="orders.loss_amount",
            filter=FilterSpec(member="orders.fraud_type", op="ne", values=["friendly_fraud"]))),
        make_metric("fraud_rate", "percent", measure=Measure(agg="avg", field="orders.is_fraud")),
        make_metric("order_count", "count", measure=Measure(agg="count", dataset="orders")),
        make_metric("order_value", "currency",
                    measure=Measure(agg="sum", field="orders.order_value")),
        make_metric("dealer_count", "count",
                    measure=Measure(agg="count_distinct", field="dealers.dealer_id")),
        make_metric("loss_per_order", ratio=Ratio(numerator="fraud_loss",
                                                  denominator="order_count")),
        make_metric("fraud_loss_k", measure=Measure(agg="sum", field="orders.loss_k")),
    ]
    return SemanticModel(name="duckdb_demo", model_version=1, valid_at=T0, known_at=T0,
                         source="duckdb_demo", dialect="duckdb", datasets=datasets,
                         relationships=[rel], metrics=sorted(metrics, key=lambda m: m.id))


def replace_dataset(model: SemanticModel, name: str, **update) -> SemanticModel:
    datasets = [d.model_copy(update=update) if d.name == name else d for d in model.datasets]
    return model.model_copy(update={"datasets": datasets})


def add_metric(model: SemanticModel, metric: Metric) -> SemanticModel:
    metrics = sorted([*(m for m in model.metrics if m.id != metric.id), metric], key=lambda m: m.id)
    return model.model_copy(update={"metrics": metrics})


def payload(metric_id: str, fmt="number", **form) -> MetricBindingPayload:
    return MetricBindingPayload(name=metric_id, label=metric_id, format=fmt, **form)


@pytest.fixture
def model() -> SemanticModel:
    return build_model()


@pytest.fixture
def duck():
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE main.orders (order_id TEXT, order_created_date DATE, sales_channel TEXT,"
        " dealer_id TEXT, loss_amount DOUBLE, is_fraud INTEGER, fraud_type TEXT,"
        " order_value DOUBLE)"
    )
    con.executemany("INSERT INTO main.orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ORDERS)
    con.execute("CREATE TABLE main.dealers (dealer_id TEXT, region TEXT)")
    con.executemany("INSERT INTO main.dealers VALUES (?, ?)", DEALERS)
    yield con
    con.close()


def run(compiled, con) -> list[tuple]:
    return con.execute(compiled.sql, compiled.params).fetchall()
