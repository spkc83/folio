"""The telco demo model as typed graph nodes (spec 4.3).

The model matches the demo data: an ``orders`` table and a ``dealers`` table, joined many
to one on ``dealer_id``.
"""

from datetime import datetime

from living_reports.semantic.graph import FolioGraph
from living_reports.semantic.kinds import (
    DatasetPayload,
    DimensionSpec,
    FieldPayload,
    FieldType,
    Measure,
    MetricBindingPayload,
    RelationshipPayload,
    SourcePayload,
)

DEMO_SOURCE = "duckdb_demo"
DEMO_START = datetime(2024, 1, 1)

# name -> (type, dimension, label)
_ORDER_FIELDS: dict[str, tuple[FieldType, DimensionSpec | None, str | None]] = {
    "order_id": ("string", None, None),
    "order_created_date": ("datetime", DimensionSpec(is_time=True), "Order date"),
    "sales_channel": ("string", DimensionSpec(), "Sales channel"),
    "fraud_type": ("string", DimensionSpec(), "Fraud type"),
    "payment_method": ("string", DimensionSpec(), "Payment method"),
    "tenure_band": ("string", DimensionSpec(), "Tenure band"),
    "dealer_id": ("string", None, None),
    "loss_amount": ("decimal", None, None),
    "is_fraud": ("integer", None, None),
    "order_value": ("decimal", None, None),
}
_DEALER_FIELDS: dict[str, tuple[FieldType, DimensionSpec | None, str | None]] = {
    "dealer_id": ("string", None, None),
    "region": ("string", DimensionSpec(), "Region"),
}
_SYNONYMS = {"orders.fraud_type": ["fraud category"]}

_METRICS = {
    "fraud_loss": (
        "Fraud loss",
        "currency",
        "Total loss on orders confirmed as fraud.",
        Measure(agg="sum", field="orders.loss_amount"),
    ),
    "fraud_rate": (
        "Fraud rate",
        "percent",
        "Share of orders confirmed as fraud.",
        Measure(agg="avg", field="orders.is_fraud"),
    ),
    "order_count": (
        "Orders",
        "count",
        "Number of orders.",
        Measure(agg="count", dataset="orders"),
    ),
    "order_value": (
        "Order value",
        "currency",
        "Total value of orders.",
        Measure(agg="sum", field="orders.order_value"),
    ),
    "dealer_count": (
        "Dealers",
        "count",
        "Number of dealers with at least one order in the model.",
        Measure(agg="count_distinct", field="dealers.dealer_id"),
    ),
}


# The better direction and the target of each demo metric (see MetricBindingPayload).
_BUSINESS = {
    "fraud_loss": ("lower", None),
    "fraud_rate": ("lower", 0.03),
    "order_count": ("higher", None),
    "order_value": ("higher", None),
}


def _metric_payload(metric_id: str) -> MetricBindingPayload:
    label, fmt, definition, measure = _METRICS[metric_id]
    better, target = _BUSINESS.get(metric_id, (None, None))
    return MetricBindingPayload(name=metric_id, label=label, format=fmt, definition=definition,
                                measure=measure, better=better, target=target)


def _add_business(fg: FolioGraph) -> None:
    """Give a graph seeded before metrics had a direction the directions and the target, once.

    The graph is old when no demo metric has a direction. After the first upgrade, or after a
    person sets a direction, the graph is not old, so a later edit stays as the person left it."""
    now = fg.slice()
    nodes = {m: now.node(m) for m in _BUSINESS}
    if any(n is not None and n.payload.get("better") is not None for n in nodes.values()):
        return
    with fg.backend.transaction():
        for metric_id, node in nodes.items():
            if node is None:
                continue
            fresh = _metric_payload(metric_id)
            payload = MetricBindingPayload(**node.payload)
            fg.put(metric_id, "MetricBinding",
                   payload.model_copy(update={"better": fresh.better, "target": fresh.target}),
                   reason="demo seed: better direction and target")


def seed_demo(
    fg: FolioGraph, *, source_id: str = DEMO_SOURCE, dialect: str = "duckdb", schema: str = "main"
) -> None:
    """Write the demo model on ``source_id`` once. A second call changes nothing.

    The demo model has one set of node ids. A seed for another source id points the same
    datasets at that source (a Postgres copy of the demo data, for example).
    """
    if fg.slice().node(source_id) is not None:
        _add_business(fg)
        return

    def put(node_id: str, kind: str, payload) -> None:
        fg.put(node_id, kind, payload, reason="demo seed", valid_from=DEMO_START)

    with fg.backend.transaction():
        put(source_id, "Source", SourcePayload(name=source_id, dialect=dialect))
        put(
            "orders",
            "Dataset",
            DatasetPayload(
                name="orders",
                source_id=source_id,
                table=f"{schema}.orders",
                primary_key=["order_id"],
                time_field="order_created_date",
            ),
        )
        put(
            "dealers",
            "Dataset",
            DatasetPayload(
                name="dealers",
                source_id=source_id,
                table=f"{schema}.dealers",
                primary_key=["dealer_id"],
            ),
        )
        for dataset, fields in (("orders", _ORDER_FIELDS), ("dealers", _DEALER_FIELDS)):
            for name, (ftype, dim, label) in fields.items():
                member = f"{dataset}.{name}"
                payload = FieldPayload(
                    name=name,
                    dataset=dataset,
                    column=name,
                    type=ftype,
                    label=label,
                    dimension=dim,
                    synonyms=_SYNONYMS.get(member, []),
                )
                put(member, "Field", payload)
        put(
            "orders_dealer",
            "Relationship",
            RelationshipPayload(
                name="orders_dealer",
                from_dataset="orders",
                to_dataset="dealers",
                from_columns=["dealer_id"],
                to_columns=["dealer_id"],
            ),
        )
        for metric_id in _METRICS:
            put(metric_id, "MetricBinding", _metric_payload(metric_id))
