"""PostgresSource against a fake psycopg connection: the SQL it sends, and its caching."""
from datetime import datetime

import psycopg
import pytest

from living_reports.sources import data_time_of, rows_of, truncated
from living_reports.sources import postgres as pg
from living_reports.sources.postgres import PostgresSource

NOW = datetime(2026, 1, 2, 3, 4, 5)


class FakeCursor:
    def __init__(self, conn, rows=()):
        self.conn, self.rows, self.description = conn, list(rows), None

    def execute(self, sql, params=None):
        self.conn.sent.append((sql, params))
        self.rows = self.conn.answer(sql)
        self.description = [type("D", (), {"name": "n"})()]
        return self

    def fetchmany(self, n):
        return self.rows[:n]

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, log):
        self.sent = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def answer(self, sql):
        if "now()" in sql:
            return [(NOW,)]
        if "information_schema.tables" in sql:
            return [("public", "orders")]
        if "information_schema.columns" in sql:
            return [("public", "orders", "order_id", "text", "NO")]
        if "reltuples" in sql:
            return [("public", "orders", 3000.0)]
        if sql.startswith("SELECT * FROM ("):
            return [(i,) for i in range(5)]
        return []

    def execute(self, sql, params=None):
        return FakeCursor(self).execute(sql, params)

    def cursor(self):
        return FakeCursor(self)


@pytest.fixture
def sent(monkeypatch):
    log, connects = [], []

    def fake_connect(dsn, **kwargs):
        connects.append(kwargs)
        return FakeConn(log)

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    return log, connects


def test_run_wraps_the_statement_in_a_limit(sent):
    log, _ = sent
    t = PostgresSource("wh", "postgresql://u@h/db").run(
        "SELECT a FROM t WHERE b = %s ORDER BY a", ["x"], limit=3)
    wrapped = [s for s, _ in log if s.startswith("SELECT * FROM (")]
    assert wrapped == ["SELECT * FROM (SELECT a FROM t WHERE b = %s ORDER BY a) AS q LIMIT 4"]
    assert [p for s, p in log if s == wrapped[0]] == [["x"]]
    assert t.num_rows == 3 and truncated(t)


def test_run_records_the_data_time_it_read(sent):
    t = PostgresSource("wh", "postgresql://u@h/db").run("SELECT a FROM t", limit=10)
    assert rows_of(t) == [[0], [1], [2], [3], [4]] and not truncated(t)
    assert data_time_of(t) == NOW


def test_introspect_is_cached_counts_from_reltuples_and_has_a_timeout(sent, monkeypatch):
    log, connects = sent
    clock = [1000.0]
    monkeypatch.setattr(pg.time, "monotonic", lambda: clock[0])
    source = PostgresSource("wh", "postgresql://u@h/db")
    catalog = source.introspect()
    assert catalog.table("public.orders").row_count == 3000
    assert not any("count(*)" in s for s, _ in log)
    assert any(s.startswith("SET statement_timeout") for s, _ in log)
    source.introspect()
    assert len(connects) == 1
    clock[0] += pg.CATALOG_TTL_S + 1
    source.introspect()
    assert len(connects) == 2
    source.refresh()
    source.introspect()
    assert len(connects) == 3
