"""The demo setting: which demo the app seeds, where its data lives, and how it is built."""
from pathlib import Path

import pytest
import yaml

from living_reports.config import get_settings
from living_reports.demos import get_demo
from living_reports.engine import Query
from living_reports.metrics.runner import run_query
from living_reports.sources.build import build_demo_source, demo_moment
from living_reports.sources.registry import reset_sources, sources
from living_reports.store import get_store, reset_store


def _demo(name, tmp_path, monkeypatch):
    """The demo ``name`` in an empty data directory, with no LR_SOURCE_PATH."""
    monkeypatch.setenv("LR_DEMO", name)
    monkeypatch.setenv("LR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LR_SOURCE_PATH", raising=False)
    reset_sources()
    reset_store()
    store = get_store()
    get_demo().seed(store.graph)
    return store


@pytest.fixture
def retail(tmp_path, monkeypatch):
    return _demo("retail", tmp_path, monkeypatch)


def test_retail_is_the_default_demo_with_its_own_data_folder(monkeypatch):
    for var in ("LR_DEMO", "LR_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    settings = get_settings()
    assert settings.demo == "retail" and settings.data_dir == Path(".folio-data/retail")
    monkeypatch.setenv("LR_DEMO", "hr")
    assert get_settings().data_dir == Path(".folio-data/hr")
    monkeypatch.setenv("LR_DATA_DIR", "/elsewhere")
    assert get_settings().data_dir == Path("/elsewhere")


def test_a_generated_demo_has_a_source_with_no_source_path(retail):
    assert sources() == ["duckdb_demo"]


def test_the_first_build_generates_the_data_then_reuses_it(retail):
    build_demo_source(1)
    folder = get_settings().data_dir.resolve() / "demo" / "retail"
    assert (folder / "sales.parquet").exists() and demo_moment(retail) == 1
    stamp = (folder / "sales.parquet").stat().st_mtime
    build_demo_source(2)
    assert (folder / "sales.parquet").stat().st_mtime == stamp and demo_moment(retail) == 2


@pytest.mark.parametrize("name", ["retail", "fraud", "hr"])
def test_every_eval_question_of_a_demo_runs_and_finds_rows(name, tmp_path, monkeypatch):
    store = _demo(name, tmp_path, monkeypatch)
    build_demo_source(2)
    items = yaml.safe_load(get_demo().questions.read_text())
    reports = [i for i in items if i["expected_kind"] == "report"]
    assert len(reports) >= 20
    assert {i["expected_kind"] for i in items} == {"report", "clarify", "out_of_scope"}
    for item in reports:
        result = run_query(Query.model_validate(item["expected_query"]), "duckdb_demo", store)
        assert result.rows, item["question"]


@pytest.mark.parametrize("name", ["retail", "fraud", "hr"])
def test_the_definition_change_names_a_metric_and_a_field_of_the_demo(name, tmp_path, monkeypatch):
    store = _demo(name, tmp_path, monkeypatch)
    change = get_demo().definition
    model = store.graph.model()
    assert model.metric(change.metric).measure is not None
    assert change.filter.member in {f"{d.name}.{f.name}" for d in model.datasets for f in d.fields}


def test_the_orders_demo_needs_the_source_path(monkeypatch, tmp_path):
    monkeypatch.setenv("LR_DEMO", "orders")
    monkeypatch.delenv("LR_SOURCE_PATH", raising=False)
    monkeypatch.setenv("LR_DATA_DIR", str(tmp_path))
    reset_sources()
    assert sources() == []


@pytest.mark.parametrize("name, column, title", [
    ("retail", "promotion_code", "Revenue: exclude returned sales"),
    ("fraud", "three_ds_result", "Fraud loss: exclude lost or stolen cards"),
    ("hr", "engagement_score", "Leavers: exclude retirements"),
])
def test_new_data_brings_december_a_new_column_and_the_definition_change(name, column, title, tmp_path, monkeypatch):
    from living_reports.harness.builder import AskContext
    from living_reports.reports.changes import advance_demo
    from living_reports.reports.store import ReportStore

    store = _demo(name, tmp_path, monkeypatch)
    build_demo_source(1)
    items = advance_demo(AskContext(store=store, reports=ReportStore(store), source_id="duckdb_demo"))
    kinds = {i.kind for i in items}
    assert {"new_data", "new_column", "new_value", "definition"} <= kinds
    assert any(i.kind == "new_column" and column in (i.title + str(i.subject)) for i in items)
    assert any(i.kind == "definition" and i.title == title for i in items)
    assert demo_moment(store) == 2
