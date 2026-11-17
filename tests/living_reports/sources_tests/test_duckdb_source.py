import os
import threading
import time
from datetime import datetime, timezone

import duckdb
import pyarrow as pa
import pytest

from living_reports.sources import (
    DuckDBSource,
    ForeignKey,
    SourceError,
    SourceTimeout,
    rows_of,
    truncated,
)

from .conftest import ORDERS_ONLY, write_duck_file

COUNT = "SELECT count(*) AS n FROM main.orders"


def test_run_returns_arrow(source):
    t = source.run(COUNT)
    assert isinstance(t, pa.Table)
    assert rows_of(t) == [[3000]]
    assert truncated(t) is False


def test_params_are_bound(source):
    t = source.run("SELECT count(*) FROM main.orders WHERE sales_channel = ?", ["Marketplace"])
    assert rows_of(t) == [[50]]


def test_empty_result_keeps_the_schema(source):
    t = source.run("SELECT order_id FROM main.orders WHERE order_id = 'none'")
    assert t.num_rows == 0
    assert t.column_names == ["order_id"]


def test_row_cap_reads_one_batch(source):
    t = source.run("SELECT order_id FROM main.orders", limit=10)
    assert t.num_rows == 10
    assert truncated(t)
    start = time.perf_counter()
    t = source.run("SELECT range AS n FROM range(200000000)", limit=10)
    assert t.num_rows == 10 and truncated(t)
    assert time.perf_counter() - start < 2


@pytest.mark.parametrize(
    "sql", ["SELECT 1; SELECT 2", "PRAGMA version", "COPY main.orders TO '/tmp/x.csv'"]
)
def test_one_statement_only(source, sql):
    with pytest.raises(SourceError):
        source.run(sql)


@pytest.mark.parametrize(
    "sql",
    ["CREATE TABLE t AS SELECT 1", "SELECT * FROM read_csv('/etc/passwd')", "INSTALL httpfs"],
)
def test_read_only_and_no_file_access(source, sql):
    with pytest.raises(SourceError):
        source.run(sql)


def test_timeout_interrupts_and_source_survives(source):
    slow = (
        "SELECT count(*) FROM range(100000000) a, range(100000) b "
        "WHERE a.range + b.range = -1"
    )
    with pytest.raises(SourceTimeout, match="test did not answer within 0.2 s"):
        source.run(slow, timeout_s=0.2)
    assert rows_of(source.run("SELECT 1")) == [[1]]


def test_as_of_is_refused(source):
    with pytest.raises(SourceError, match="does not support as-of reads"):
        source.run("SELECT 1", as_of=datetime(2025, 1, 1))


def test_introspect_catalog(source):
    c = source.introspect()
    assert {t.table for t in c.tables} == {"main.orders", "main.dealers"}
    assert c.dialect == "duckdb" and c.source_id == "test"
    o = c.table("main.orders")
    assert o.row_count == 3000
    assert o.column("sales_channel").type == "string"
    assert o.column("loss_amount").type == "decimal"
    assert o.column("order_created_date").type == "datetime"
    samples = o.column("sales_channel").samples
    assert set(samples) <= {"App", "Web", "Telesales", "Marketplace"} and len(samples) == 3
    assert o.column("sales_channel").distinct_count in (4, 5)
    assert o.column("sales_channel").null_rate == 0.0
    assert c.table("main.dealers").row_count == 7


def test_introspect_declared_keys(tmp_path):
    path = tmp_path / "keys.duckdb"
    with duckdb.connect(str(path)) as conn:
        conn.execute("CREATE TABLE d(id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE o(id INTEGER, d_id INTEGER REFERENCES d(id))")
    s = DuckDBSource("keys", path)
    c = s.introspect()
    assert c.table("main.d").primary_key == ["id"]
    assert c.table("main.o").foreign_keys == [
        ForeignKey(columns=["d_id"], to_table="main.d", to_columns=["id"])
    ]
    s.close()


def test_data_time_is_the_file_time(source, duck_file):
    expected = datetime.fromtimestamp(duck_file.stat().st_mtime, timezone.utc).replace(
        tzinfo=None
    )
    assert source.data_time() == expected


def test_reopen_after_rebuild(source, duck_file, tmp_path):
    assert rows_of(source.run(COUNT)) == [[3000]]
    before = source.data_time()
    time.sleep(0.01)
    fresh = write_duck_file(
        tmp_path / "fresh.duckdb", orders_sql=f"SELECT * FROM ({ORDERS_ONLY}) LIMIT 10"
    )
    os.replace(fresh, duck_file)
    assert rows_of(source.run(COUNT)) == [[10]]
    assert source.data_time() != before


def test_a_running_query_finishes_on_the_old_file(source, duck_file, tmp_path):
    """A rebuild during a run: the run ends on the old file, the next run sees the new one."""
    reader = source._cursor()
    reader.execute("SELECT order_id FROM main.orders")
    fresh = write_duck_file(
        tmp_path / "fresh.duckdb", orders_sql=f"SELECT * FROM ({ORDERS_ONLY}) LIMIT 10"
    )
    os.replace(fresh, duck_file)
    assert len(reader.fetchall()) == 3000
    assert rows_of(source.run(COUNT)) == [[10]]


def test_check_names_no_path(tmp_path):
    with pytest.raises(SourceError) as error:
        DuckDBSource("ghost", tmp_path / "missing.duckdb").check()
    assert str(tmp_path) not in str(error.value)
    assert "ghost" in str(error.value)


def test_concurrent_runs_share_one_connection(source, monkeypatch):
    calls = []
    real = duckdb.connect

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", counting)
    results, errors = [], []

    def work():
        try:
            for _ in range(5):
                results.append(rows_of(source.run(COUNT))[0][0])
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert results == [3000] * 40
    assert len(calls) == 1


def test_run_stamps_the_data_time_of_the_file_it_read(source, duck_file, tmp_path):
    from living_reports.sources import data_time_of
    assert data_time_of(source.run(COUNT)) == source.data_time()
    time.sleep(0.01)
    fresh = write_duck_file(tmp_path / "fresh.duckdb", orders_sql=f"SELECT * FROM ({ORDERS_ONLY}) LIMIT 10")
    os.replace(fresh, duck_file)
    t = source.run(COUNT)
    assert rows_of(t) == [[10]] and data_time_of(t) == source.data_time()


def test_file_errors_do_not_name_the_path(source):
    with pytest.raises(SourceError) as error:
        source.run("SELECT * FROM read_csv('/etc/passwd')")
    assert "/etc/passwd" not in str(error.value) and "test" in str(error.value)
    with pytest.raises(SourceError, match="nope"):
        source.run("SELECT nope FROM main.orders")
