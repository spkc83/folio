import json

import pytest
from fastapi.testclient import TestClient

from living_reports.semantic.kinds import DatasetPayload, SourcePayload
from living_reports.sources import DuckDBSource, SourceError, rows_of
from living_reports.sources.build import build_demo_source
from living_reports.sources.registry import active_source, reset_sources, source_for, sources


def test_demo_source_is_registered_when_source_path_is_set(store):
    assert sources() == ["duckdb_demo"]
    assert source_for("duckdb_demo") is source_for("duckdb_demo")
    assert isinstance(source_for("duckdb_demo"), DuckDBSource)


def test_no_demo_without_source_path(store, monkeypatch):
    monkeypatch.delenv("LR_SOURCE_PATH")
    reset_sources()
    assert sources() == []
    with pytest.raises(SourceError, match="No source is configured for duckdb_demo"):
        source_for("duckdb_demo")


def test_sources_from_env_json(store, duck_file, orders_file, monkeypatch, tmp_path):
    config = {
        "extra": {"kind": "duckdb", "path": str(duck_file)},
        "files": {"kind": "duckdb", "files": {"orders": str(orders_file)}},
    }
    monkeypatch.setenv("LR_SOURCES", json.dumps(config))
    reset_sources()
    assert sources() == ["duckdb_demo", "extra", "files"]
    assert rows_of(source_for("extra").run("SELECT 1")) == [[1]]
    assert rows_of(source_for("files").run("SELECT count(*) FROM main.orders")) == [[3000]]
    assert (tmp_path / "lr" / "sources" / "files.duckdb").exists()


def test_a_source_config_needs_its_location(monkeypatch):
    from living_reports.config import SourceConfig
    with pytest.raises(ValueError):
        SourceConfig(kind="duckdb")
    with pytest.raises(ValueError):
        SourceConfig(kind="postgres", path="x")


def test_active_source_reads_the_model(store):
    assert active_source(store) == "duckdb_demo"
    fg = store.graph
    fg.put("other", "Source", SourcePayload(name="other"), reason="test")
    fg.put(
        "extra",
        "Dataset",
        DatasetPayload(name="extra", source_id="other", table="main.extra", primary_key=["id"]),
        reason="test",
    )
    with pytest.raises(SourceError, match="no single source"):
        active_source(store)


def test_health_lists_sources(store):
    from living_reports.api.app import create_app
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "store": "ok", "sources": {"duckdb_demo": "ok"}}


def test_health_names_a_missing_file_without_its_path(store, monkeypatch, tmp_path):
    missing = tmp_path / "nowhere" / "gone.duckdb"
    monkeypatch.setenv("LR_SOURCES", json.dumps({"gone": {"kind": "duckdb", "path": str(missing)}}))
    reset_sources()
    from living_reports.api.app import create_app
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded" and body["sources"]["duckdb_demo"] == "ok"
    assert body["sources"]["gone"]
    assert "nowhere" not in response.text


def test_startup_builds_the_demo_at_the_saved_moment(store):
    build_demo_source(2)
    reset_sources()
    from living_reports.api.app import create_app
    with TestClient(create_app()):
        count = rows_of(source_for("duckdb_demo").run("SELECT count(*) FROM main.orders"))
    assert count == [[3000]]


def test_seed_demo_for_another_source(orders_file):
    from living_reports.semantic.demo import seed_demo
    from living_reports.store import get_store
    fg = get_store().graph
    seed_demo(fg, source_id="pg_demo", dialect="postgres", schema="public")
    seed_demo(fg, source_id="pg_demo", dialect="postgres", schema="public")
    model = fg.model()
    assert model.source == "pg_demo" and model.dialect == "postgres"
    assert {d.table for d in model.datasets} == {"public.orders", "public.dealers"}
    assert len(fg.slice().nodes("MetricBinding")) == 5
