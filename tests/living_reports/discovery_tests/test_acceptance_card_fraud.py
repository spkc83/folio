"""Spec 9.5 acceptance: a second dataset with no seed code.

Connect the card-fraud file, discover, confirm, and answer; the answer matches direct SQL.
Runs only when LR_CARD_DATA_PATH names the file (demo_data).
"""
import json
import os
from pathlib import Path

import pytest

from living_reports.discovery.confirm import confirm_proposals
from living_reports.discovery.draft import DiscoveryDraft, DraftDataset, DraftMetric
from living_reports.discovery.run import discover
from living_reports.engine import Query
from living_reports.metrics.runner import run_query
from living_reports.semantic.kinds import SourcePayload
from living_reports.semantic.ossie import document_to_changes, export_model, parse_document
from living_reports.sources import rows_of
from living_reports.sources.registry import reset_sources, source_for
from living_reports.store import get_store, reset_store

from .conftest import context

pytestmark = pytest.mark.demo_data

TOP_4 = [("Travel", 0.0342), ("Electronics", 0.0281), ("Fuel", 0.0265), ("DigitalGoods", 0.0254)]


@pytest.fixture
def card_fraud_store(monkeypatch, tmp_path):
    path = os.environ.get("LR_CARD_DATA_PATH")
    if not path or not Path(path).expanduser().is_file():
        pytest.skip("LR_CARD_DATA_PATH does not name the card transactions Parquet file")
    monkeypatch.delenv("LR_SOURCE_PATH", raising=False)
    monkeypatch.setenv("LR_DATA_DIR", str(tmp_path / "lr"))
    monkeypatch.setenv("LR_SOURCES", json.dumps(
        {"card_fraud": {"kind": "duckdb", "files": {"transactions": path}}}))
    reset_sources()
    reset_store()
    return get_store()


DRAFT = DiscoveryDraft(
    datasets=[DraftDataset(
        name="transactions", time_field="transactions.transaction_ts",
        dimensions=["transactions.merchant_category", "transactions.entry_mode",
                    "transactions.card_type"])],
    metrics=[
        DraftMetric(name="fraud_rate", label="Fraud rate", format="percent", agg="avg",
                    field="transactions.is_fraud"),
        DraftMetric(name="transaction_count", label="Transactions", format="count", agg="count",
                    dataset="transactions"),
    ],
)


def _discover_and_confirm(store):
    ctx = context(store)
    ctx.source_id = "card_fraud"
    run = discover("card_fraud", ctx, draft=DRAFT)
    assert run.dropped == []
    assert confirm_proposals(None, "omar", store).failed == []
    return run


def test_connect_discover_confirm_and_answer_matches_direct_sql(card_fraud_store):
    store = card_fraud_store
    _discover_and_confirm(store)
    q = Query(metrics=["fraud_rate"], dimensions=["transactions.merchant_category"],
              order=[["fraud_rate", "desc"]], limit=4)
    result = run_query(q, "card_fraud", store)
    assert [(r[0], round(r[1], 4)) for r in result.rows] == TOP_4
    direct = rows_of(source_for("card_fraud").run(
        "SELECT merchant_category, avg(is_fraud) AS r FROM main.transactions "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 4"))
    assert [(r[0], round(r[1], 4)) for r in direct] == TOP_4
    model = store.graph.model()
    assert model.metric("fraud_rate").version == 1
    assert model.dataset("transactions").primary_key == []
    tid = model.field("transactions.transaction_id")
    assert tid.dimension is None and tid.expression is None
    assert model.source == "card_fraud"


def test_export_of_the_discovered_model_reimports_clean(card_fraud_store):
    store = card_fraud_store
    _discover_and_confirm(store)
    changes, dropped = document_to_changes(
        parse_document(export_model(store.graph.model())), SourcePayload(name="card_fraud"))
    assert dropped == []
    live = store.graph.slice()
    assert all(live.node(c.id) is not None for c in changes)
