from datetime import date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from living_reports.sources import (
    Catalog,
    ColumnInfo,
    SourceError,
    TableInfo,
    field_type,
    normalize,
    refuse_as_of,
    rows_of,
    truncated,
)


def test_field_type_mapping():
    assert field_type("VARCHAR") == "string"
    assert field_type("TIMESTAMP WITH TIME ZONE") == "datetime"
    assert field_type("DECIMAL(18,2)") == "decimal"
    assert field_type("int4") == "integer"
    assert field_type("character varying") == "string"
    assert field_type("double precision") == "decimal"
    assert field_type("DATE") == "date"
    assert field_type("BLOB") is None


def test_normalize_casts_decimals_and_dates():
    t = pa.table({
        "d": pa.array([Decimal("1.50")], pa.decimal128(18, 2)),
        "ts": pa.array([datetime(2025, 1, 15, 10)], pa.timestamp("us")),
        "day": pa.array([date(2025, 2, 1)], pa.date32()),
    })
    assert rows_of(normalize(t)) == [[1.5, "2025-01-15", "2025-02-01"]]


def test_rows_of_empty_table():
    assert rows_of(pa.table({"a": pa.array([], pa.int64())})) == []


def test_truncated_flag_round_trips():
    t = pa.table({"a": [1]})
    assert truncated(t.replace_schema_metadata({b"truncated": b"1"})) is True
    assert truncated(t) is False


def test_catalog_lookups():
    catalog = Catalog(
        source_id="s",
        dialect="duckdb",
        taken_at=datetime(2025, 1, 1),
        tables=[
            TableInfo(schema_name="main", name="orders", columns=[
                ColumnInfo(name="order_id", source_type="VARCHAR", type="string"),
                ColumnInfo(name="loss_amount", source_type="DECIMAL(18,2)", type="decimal"),
            ]),
            TableInfo(schema_name="main", name="dealers", columns=[
                ColumnInfo(name="dealer_id", source_type="VARCHAR", type="string"),
            ]),
        ],
    )
    assert catalog.table("main.orders").table == "main.orders"
    assert catalog.table("main.orders").column("loss_amount").type == "decimal"
    assert catalog.members == ["dealers.dealer_id", "orders.loss_amount", "orders.order_id"]
    with pytest.raises(KeyError):
        catalog.table("main.nope")
    with pytest.raises(KeyError):
        catalog.table("main.orders").column("nope")


class _Stub:
    id = "stub"
    supports_as_of = False


def test_refuse_as_of():
    with pytest.raises(SourceError, match="does not support as-of reads"):
        refuse_as_of(_Stub(), datetime(2025, 1, 1))
    refuse_as_of(_Stub(), None)


def test_pins_are_installed():
    import psycopg
    assert pa.__version__ == "26.0.1"
    assert psycopg.__version__.startswith("3.3.")
