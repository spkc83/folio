"""Switch the demo dataset in a running server: each demo keeps its own data folder."""
from pathlib import Path

import pytest

from living_reports.config import get_settings
from living_reports.demos.switch import DATASETS, DemoSwitchError, datasets, switch_demo
from living_reports.sources.build import demo_moment
from living_reports.sources.registry import reset_sources, sources
from living_reports.store import get_store, reset_store


@pytest.fixture
def retail(tmp_path, monkeypatch):
    """The retail demo with the default data folder, under a new working folder."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LR_DEMO", "retail")
    for var in ("LR_DATA_DIR", "LR_SOURCE_PATH"):
        monkeypatch.delenv(var, raising=False)
    reset_sources()
    reset_store()
    yield get_store()
    reset_sources()
    reset_store()


def test_a_switch_seeds_and_builds_the_new_demo_then_selects_it(retail):
    switch_demo("hr")
    assert get_settings().demo == "hr"
    assert get_settings().data_dir == Path(".folio-data/hr")
    store = get_store()
    assert store is not retail
    assert store.graph.model().metric("attrition_rate").measure is not None
    assert demo_moment(store) == 1 and sources() == ["duckdb_demo"]


def test_a_switch_back_finds_the_first_demo_s_store_again(retail):
    retail.execute("INSERT OR REPLACE INTO app_state VALUES ('marker', 'retail')")
    switch_demo("fraud")
    switch_demo("retail")
    assert get_store().row("SELECT value FROM app_state WHERE key = 'marker'") == ("retail",)


def test_a_fixed_data_folder_cannot_switch(retail, monkeypatch, tmp_path):
    monkeypatch.setenv("LR_DATA_DIR", str(tmp_path / "fixed"))
    assert datasets() == []
    with pytest.raises(DemoSwitchError):
        switch_demo("hr")
    assert get_settings().demo == "retail"


def test_only_the_generated_demos_are_offered(retail):
    assert [d["name"] for d in datasets()] == ["retail", "fraud", "hr"] == list(DATASETS)
    assert datasets()[0] == {"name": "retail", "title": "Retail sales"}
    with pytest.raises(DemoSwitchError):
        switch_demo("orders")
