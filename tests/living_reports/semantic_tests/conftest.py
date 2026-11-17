from datetime import datetime

import pytest

from folio_graph import SQLiteBackend
from living_reports.semantic.graph import FolioGraph
from living_reports.semantic.kinds import (
    DatasetPayload,
    DimensionSpec,
    FieldPayload,
    Measure,
    MetricBindingPayload,
    SourcePayload,
)


class Clock:
    """A clock that tests move by hand."""

    def __init__(self, start: datetime):
        self.now = start

    def set(self, when: datetime) -> None:
        self.now = when

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2024, 1, 1))


@pytest.fixture
def fg(clock) -> FolioGraph:
    return FolioGraph(SQLiteBackend(), clock=clock)


def source() -> SourcePayload:
    return SourcePayload(name="duckdb_demo")


def orders_dataset() -> DatasetPayload:
    return DatasetPayload(
        name="orders",
        source_id="duckdb_demo",
        table="main.orders",
        primary_key=["order_id"],
        time_field="order_created_date",
    )


def dealers_dataset() -> DatasetPayload:
    return DatasetPayload(
        name="dealers", source_id="duckdb_demo", table="main.dealers", primary_key=["dealer_id"]
    )


def field(
    name: str, type: str = "string", dimension: DimensionSpec | None = None, dataset="orders"
) -> FieldPayload:
    return FieldPayload(name=name, dataset=dataset, column=name, type=type, dimension=dimension)


def fraud_loss(field: str = "orders.loss_amount", filter=None) -> MetricBindingPayload:
    return MetricBindingPayload(
        name="fraud_loss",
        label="Fraud loss",
        format="currency",
        definition="Total loss on orders confirmed as fraud.",
        measure=Measure(agg="sum", field=field, filter=filter),
    )
