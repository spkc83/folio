"""Discovery fixtures: hand-built catalogs, a draft, and a DuckDB file with declared keys."""
from datetime import datetime

import pytest

from living_reports.sources.base import Catalog, ColumnInfo, ForeignKey, TableInfo


def _col(name, type_, distinct=None, source_type=None):
    return ColumnInfo(
        name=name,
        source_type=source_type or (type_ or "BLOB").upper(),
        type=type_,
        distinct_count=distinct,
        null_rate=0.0,
        samples=[],
    )


def make_catalog(fk: bool = False) -> Catalog:
    orders = TableInfo(
        schema_name="main",
        name="orders",
        row_count=3000,
        primary_key=["order_id"],
        columns=[
            _col("order_id", "string", 3000),
            _col("order_created_date", "datetime", 3000),
            _col("sales_channel", "string", 4),
            _col("dealer_id", "string", 7),
            _col("loss_amount", "decimal", 2),
            _col("is_fraud", "integer", 2),
            _col("fraud_type", "string", 3),
            _col("blob_col", None, source_type="BLOB"),
        ],
        foreign_keys=(
            [ForeignKey(columns=["dealer_id"], to_table="main.dealers", to_columns=["dealer_id"])]
            if fk
            else []
        ),
    )
    dealers = TableInfo(
        schema_name="main",
        name="dealers",
        row_count=7,
        primary_key=["dealer_id"],
        columns=[_col("dealer_id", "string", 7), _col("region", "string", 2)],
    )
    return Catalog(
        source_id="test", dialect="duckdb", taken_at=datetime(2026, 1, 1), tables=[dealers, orders]
    )


@pytest.fixture
def catalog() -> Catalog:
    return make_catalog()


@pytest.fixture
def catalog_with_fk() -> Catalog:
    return make_catalog(fk=True)


def make_draft():
    from living_reports.discovery.draft import (
        DiscoveryDraft,
        DraftDataset,
        DraftMetric,
        DraftRelationship,
    )

    return DiscoveryDraft(
        datasets=[
            DraftDataset(
                name="orders",
                time_field="orders.order_created_date",
                dimensions=["orders.sales_channel", "orders.fraud_type"],
            ),
            DraftDataset(name="dealers", dimensions=["dealers.region"]),
        ],
        relationships=[
            DraftRelationship(
                name="orders_dealer", from_dataset="orders", to_dataset="dealers",
                from_columns=["dealer_id"], to_columns=["dealer_id"], confidence=0.9,
            )
        ],
        metrics=[
            DraftMetric(name="fraud_loss", label="Fraud loss", format="currency", agg="sum",
                        field="orders.loss_amount",
                        definition="Total loss on orders confirmed as fraud.",
                        synonyms=["loss"]),
            DraftMetric(name="fraud_rate", label="Fraud rate", format="percent", agg="avg",
                        field="orders.is_fraud"),
            DraftMetric(name="order_count", label="Orders", format="count", agg="count",
                        dataset="orders"),
            DraftMetric(name="dealer_count", label="Dealers", format="count",
                        agg="count_distinct", field="dealers.dealer_id"),
        ],
    )


@pytest.fixture
def draft():
    return make_draft()


# A DuckDB file like the sources fixture (main.orders without region, main.dealers), with
# declared primary keys, so the catalog gives keys the way a real database does.
def write_keyed_file(path, duplicate_dealer: bool = False):
    import duckdb

    from ..sources_tests.conftest import DEALERS_ONLY, ORDERS_ONLY

    with duckdb.connect(str(path)) as conn:
        conn.execute(f"CREATE TABLE main.orders_src AS {ORDERS_ONLY}")
        cols = conn.execute("DESCRIBE main.orders_src").fetchall()
        body = ", ".join(f'"{c[0]}" {c[1]}' for c in cols)
        conn.execute(f"CREATE TABLE main.orders ({body}, PRIMARY KEY (order_id))")
        conn.execute("INSERT INTO main.orders SELECT * FROM main.orders_src")
        conn.execute("DROP TABLE main.orders_src")
        if duplicate_dealer:
            conn.execute(
                f"CREATE TABLE main.dealers AS {DEALERS_ONLY.replace('ORDER BY dealer_id', '')}"
                " UNION ALL SELECT 'D1', 'West'"
            )
        else:
            conn.execute(
                "CREATE TABLE main.dealers (dealer_id VARCHAR PRIMARY KEY, region VARCHAR)"
            )
            conn.execute(f"INSERT INTO main.dealers {DEALERS_ONLY}")
    return path


@pytest.fixture
def keyed_file(tmp_path):
    return write_keyed_file(tmp_path / "test.duckdb")


@pytest.fixture
def dup_file(tmp_path):
    return write_keyed_file(tmp_path / "dup.duckdb", duplicate_dealer=True)


@pytest.fixture
def source(keyed_file):
    from living_reports.sources import DuckDBSource

    s = DuckDBSource("test", keyed_file)
    yield s
    s.close()


@pytest.fixture
def dup_source(dup_file):
    from living_reports.sources import DuckDBSource

    s = DuckDBSource("test", dup_file)
    yield s
    s.close()


OWNER, AUTHOR = {"X-LR-User": "omar"}, {"X-LR-User": "ana"}


def use_source(monkeypatch, tmp_path, path, source_id="test"):
    """Configure ``source_id`` on ``path`` and no demo source, with a fresh data dir."""
    import json

    from living_reports.sources.registry import reset_sources
    from living_reports.store import reset_store

    monkeypatch.delenv("LR_SOURCE_PATH", raising=False)
    monkeypatch.setenv("LR_DATA_DIR", str(tmp_path / "lr"))
    monkeypatch.setenv("LR_SOURCES", json.dumps({source_id: {"kind": "duckdb", "path": str(path)}}))
    reset_sources()
    reset_store()


@pytest.fixture
def empty_store(monkeypatch, tmp_path, keyed_file):
    """A store with no demo seed and the ``test`` source on the keyed file."""
    from living_reports.store import get_store

    use_source(monkeypatch, tmp_path, keyed_file)
    return get_store()


@pytest.fixture
def dup_store(monkeypatch, tmp_path, dup_file):
    from living_reports.store import get_store

    use_source(monkeypatch, tmp_path, dup_file)
    return get_store()


def context(store, user="omar"):
    from living_reports.harness.builder import AskContext
    from living_reports.reports.store import ReportStore

    return AskContext(store=store, reports=ReportStore(store), source_id="test", user=user)


def pid_of(store, node_id):
    return next(p.id for p in store.graph.pending() if p.change.id == node_id)
