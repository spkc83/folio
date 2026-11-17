"""Fixtures for the source tests: a built DuckDB file with ``main.orders`` and ``main.dealers``."""
import duckdb
import pytest

from ..conftest import ORDERS_SQL

ORDERS_ONLY = f"SELECT * EXCLUDE (region) FROM ({ORDERS_SQL})"
DEALERS_ONLY = f"SELECT DISTINCT dealer_id, region FROM ({ORDERS_SQL}) ORDER BY dealer_id"


def write_duck_file(path, orders_sql=ORDERS_ONLY, dealers_sql=DEALERS_ONLY):
    with duckdb.connect(str(path)) as conn:
        conn.execute(f"CREATE TABLE main.orders AS {orders_sql}")
        conn.execute(f"CREATE TABLE main.dealers AS {dealers_sql}")
    return path


@pytest.fixture
def duck_file(tmp_path):
    return write_duck_file(tmp_path / "test.duckdb")


@pytest.fixture
def source(duck_file):
    from living_reports.sources import DuckDBSource
    s = DuckDBSource("test", duck_file)
    yield s
    s.close()


NO_SERVER = "LR_PG_DSN is not set; no Postgres server for this run"


@pytest.fixture
def pg_dsn():
    import os
    dsn = os.environ.get("LR_PG_DSN")
    if not dsn:
        pytest.skip(NO_SERVER)
    return dsn


@pytest.fixture
def pg_source(pg_dsn):
    from living_reports.sources.postgres import PostgresSource
    return PostgresSource("pg_test", pg_dsn)


@pytest.fixture
def pg_fixture_tables(pg_dsn, tmp_path):
    """Schema folio_test with orders and dealers from the fixture rows (moment 2)."""
    import psycopg

    from scripts.load_demo_postgres import load
    parquet = tmp_path / "orders.parquet"
    duckdb.sql(f"COPY ({ORDERS_SQL}) TO '{parquet}' (FORMAT PARQUET)")
    load(parquet, pg_dsn, schema="folio_test")
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        # A real sequence: nextval on it is a write that read-only transactions refuse.
        conn.execute("CREATE SEQUENCE IF NOT EXISTS folio_test.seq")
    yield "folio_test"
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS folio_test CASCADE")
