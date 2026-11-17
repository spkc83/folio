"""PostgresSource against a live server. Every test skips when LR_PG_DSN is not set."""
import time

import pytest

from living_reports.engine import Query
from living_reports.sources import SourceError, SourceTimeout, normalize, rows_of, truncated

SPEC = {
    "metrics": ["fraud_loss"],
    "dimensions": ["orders.sales_channel"],
    "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}],
    "time": {"field": "orders.order_created_date", "start": "2025-01-01", "end": "2026-01-01"},
}


def test_run_returns_arrow_rows(pg_source, pg_fixture_tables):
    t = pg_source.run("SELECT count(*) AS n FROM folio_test.orders")
    assert rows_of(normalize(t)) == [[3000]]


def test_params_bind_with_percent_s(pg_source, pg_fixture_tables):
    t = pg_source.run("SELECT count(*) FROM folio_test.orders WHERE sales_channel = %s",
                      ["Marketplace"])
    assert rows_of(t) == [[50]]


def test_a_literal_percent_with_params(pg_source, pg_fixture_tables):
    t = pg_source.run("SELECT count(*) FROM folio_test.orders WHERE fraud_type LIKE '%theft' "
                      "AND sales_channel = %s", ["App"])
    assert rows_of(t)[0][0] > 0


@pytest.mark.parametrize("sql", ["CREATE TABLE folio_test.t (a int)",
                                 "DELETE FROM folio_test.orders"])
def test_read_only_is_enforced(pg_source, pg_fixture_tables, sql):
    with pytest.raises(SourceError):
        pg_source.run(sql)


def test_writes_inside_a_select_are_refused(pg_source, pg_fixture_tables):
    with pytest.raises(SourceError):
        pg_source.run("SELECT nextval('folio_test.seq')")


def test_statement_timeout_is_set_per_run(pg_source, pg_fixture_tables):
    with pytest.raises(SourceTimeout, match="pg_test did not answer within 0.2 s"):
        pg_source.run("SELECT pg_sleep(2)", timeout_s=0.2)
    assert rows_of(pg_source.run("SELECT 1 AS one")) == [[1]]


def test_row_cap_and_truncated(pg_source, pg_fixture_tables):
    t = pg_source.run("SELECT order_id FROM folio_test.orders", limit=10)
    assert t.num_rows == 10 and truncated(t)


def test_introspect_keys_and_stats(pg_source, pg_fixture_tables):
    c = pg_source.introspect()
    assert c.dialect == "postgres"
    assert c.table("folio_test.dealers").primary_key == ["dealer_id"]
    orders = c.table("folio_test.orders")
    assert orders.primary_key == ["order_id"] and orders.row_count == 3000
    channel = orders.column("sales_channel")
    assert channel.type == "string" and channel.distinct_count in (4, 5)
    assert channel.null_rate == 0.0 and len(channel.samples) == 3


def test_compiled_spec_query_runs_on_postgres(pg_dsn, pg_fixture_tables, store2, monkeypatch):
    import json

    from living_reports.metrics.runner import run_query
    from living_reports.semantic.demo import seed_demo
    from living_reports.sources.registry import reset_sources
    q = Query.model_validate(SPEC)
    duck = run_query(q, "duckdb_demo", store2).rows
    monkeypatch.setenv("LR_SOURCES", json.dumps({"pg_test": {"kind": "postgres", "dsn": pg_dsn}}))
    reset_sources()
    seed_demo(store2.graph, source_id="pg_test", dialect="postgres", schema="folio_test")
    pg = run_query(q, "pg_test", store2).rows
    assert [(c, round(v, 2)) for c, v in pg] == [(c, round(v, 2)) for c, v in duck]


def test_data_time_is_server_now(pg_source):
    first = pg_source.data_time()
    time.sleep(0.01)
    assert pg_source.data_time() > first
