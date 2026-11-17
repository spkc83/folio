"""PostgresSource without a server: rendering, percent signs, DSN redaction, errors."""
import json
from datetime import date

import pytest

from living_reports.engine import Filter, Query
from living_reports.engine.compile import compile
from living_reports.sources import SourceError
from living_reports.sources.postgres import PostgresSource, prepare, redact_dsn
from living_reports.sources.registry import reset_sources, source_for

from ..engine_tests.conftest import build_model

SPEC = {
    "metrics": ["fraud_loss"],
    "dimensions": ["orders.sales_channel"],
    "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}],
    "time": {"field": "orders.order_created_date", "start": "2025-01-01", "end": "2026-01-01"},
}


def test_redact_dsn():
    assert redact_dsn("postgresql://u:secret@h:5432/db") == "postgresql://u@h:5432/db"
    assert redact_dsn("host=h user=u password=secret dbname=db") == "postgresql://u@h:5432/db"
    assert redact_dsn("not a dsn ===") == "postgresql://…"
    assert "secret" not in repr(PostgresSource("x", "postgresql://u:secret@h/db"))


def test_percent_inside_literals_is_doubled():
    sql = prepare("SELECT * FROM main.orders WHERE fraud_type LIKE '%fraud' AND region = %s")
    assert "'%%fraud'" in sql and "= %s" in sql
    assert prepare("SELECT 'it''s 100%' AS t") == "SELECT 'it''s 100%%' AS t"
    assert prepare("SELECT 1") == "SELECT 1"
    assert prepare("SELECT a FROM t WHERE b LIKE %s ESCAPE '\\'") == (
        "SELECT a FROM t WHERE b LIKE %s ESCAPE '\\'"
    )


def test_a_quoted_identifier_keeps_its_percent_sign_outside_a_literal():
    # A double-quoted identifier is not a string literal; a percent sign there is doubled
    # too, because psycopg reads every % outside a parameter as a format character.
    assert prepare('SELECT 1 AS "100%" WHERE a = %s') == 'SELECT 1 AS "100%%" WHERE a = %s'


def test_percent_in_a_param_is_untouched():
    model = build_model()
    q = Query(metrics=["fraud_loss"],
              filters=[Filter(member="orders.fraud_type", op="contains", values=["50%"])])
    c = compile(q, model, "postgres")
    assert prepare(c.sql).count("%s") == 1
    assert c.params == ["%50\\%%"]


def test_compiled_query_renders_for_postgres():
    c = compile(Query.model_validate(SPEC), build_model(), "postgres")
    assert prepare(c.sql).count("%s") == 3
    assert c.params == ["East", date(2025, 1, 1), date(2026, 1, 1)]


def test_errors_never_carry_the_dsn():
    source = PostgresSource("wh", "postgresql://u:secret@127.0.0.1:1/db?connect_timeout=2")
    with pytest.raises(SourceError) as error:
        source.check()
    text = str(error.value)
    assert "secret" not in text and "127.0.0.1" not in text
    assert text == "wh: cannot connect"
    with pytest.raises(SourceError) as error:
        source.run("SELECT 1")
    assert "secret" not in str(error.value) and "127.0.0.1" not in str(error.value)


def test_run_refuses_more_than_one_select_before_connecting():
    source = PostgresSource("wh", "postgresql://u:secret@127.0.0.1:1/db?connect_timeout=2")
    for sql in ("SELECT 1; SELECT 2", "DELETE FROM t", "COPY t TO STDOUT"):
        with pytest.raises(SourceError) as error:
            source.run(sql)
        assert "cannot connect" not in str(error.value)


def test_registry_builds_postgres_source(orders_file, monkeypatch):
    monkeypatch.setenv("LR_SOURCES", json.dumps({"wh": {"kind": "postgres", "dsn": "postgresql://u:p@h/db"}}))
    reset_sources()
    source = source_for("wh")
    assert isinstance(source, PostgresSource)
    assert source.dialect == "postgres" and source.supports_as_of is False


def test_health_names_a_broken_source_without_its_dsn(store, monkeypatch):
    from fastapi.testclient import TestClient

    from living_reports.api.app import create_app
    dsn = "postgresql://user:secret@nohost.invalid:1/db?connect_timeout=2"
    monkeypatch.setenv("LR_SOURCES", json.dumps({"wh": {"kind": "postgres", "dsn": dsn}}))
    reset_sources()
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
        sources = client.get("/api/sources")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded" and body["sources"]["wh"]
    assert "secret" not in response.text and "nohost" not in response.text
    assert "secret" not in sources.text and "nohost" not in sources.text


def test_loader_maps_duckdb_types():
    from scripts.load_demo_postgres import pg_type
    assert pg_type("VARCHAR") == "TEXT"
    assert pg_type("TIMESTAMP") == "TIMESTAMP"
    assert pg_type("DECIMAL(18,2)") == "DOUBLE PRECISION"
    assert pg_type("TINYINT") == "INTEGER" and pg_type("BIGINT") == "BIGINT"
    assert pg_type("BLOB") == "TEXT"


def test_connect_has_a_timeout_and_read_only_transactions(monkeypatch):
    import psycopg
    seen = {}

    def fake_connect(dsn, **kwargs):
        seen.update(kwargs)
        raise psycopg.OperationalError("connection to server at host failed")

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    with pytest.raises(SourceError, match="wh: cannot connect"):
        PostgresSource("wh", "postgresql://u@h/db").check()
    assert seen["connect_timeout"] == 5
    assert seen["options"] == "-c default_transaction_read_only=on"
    seen.clear()
    with pytest.raises(SourceError):
        PostgresSource("wh", "postgresql://u@h/db?connect_timeout=2").check()
    assert "connect_timeout" not in seen
