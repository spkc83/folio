"""Spec 5.3 acceptance on Postgres: the real demo data loaded by the loader script.

Runs only with the real demo file (demo_data) and a server (LR_PG_DSN).
"""
import json

import pytest

from living_reports.engine import Query

pytestmark = pytest.mark.demo_data

SPEC = {
    "metrics": ["fraud_loss"],
    "dimensions": ["orders.sales_channel"],
    "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}],
    "time": {"field": "orders.order_created_date", "start": "2025-01-01", "end": "2026-01-01"},
    "order": [["fraud_loss", "desc"]],
}
_LOADED: list = []


@pytest.fixture
def pg_store(pg_dsn, real_orders, monkeypatch):
    from living_reports.semantic.demo import seed_demo
    from living_reports.sources.registry import reset_sources
    from living_reports.store import get_store, reset_store
    from scripts.load_demo_postgres import main
    if not _LOADED:
        main(real_orders, pg_dsn)
        _LOADED.append(True)
    monkeypatch.setenv("LR_SOURCES", json.dumps({"postgres_demo": {"kind": "postgres", "dsn": pg_dsn}}))
    reset_sources()
    reset_store()
    store = get_store()
    seed_demo(store.graph, source_id="postgres_demo", dialect="postgres", schema="public")
    return store


def test_spec_5_3_values_on_postgres(pg_store):
    from living_reports.metrics.runner import run_query
    rows = run_query(Query.model_validate(SPEC), "postgres_demo", pg_store).rows
    assert [(c, round(v, 2)) for c, v in rows] == [
        ("Telesales", 2807475.89), ("App", 1841271.48), ("Web", 1474301.94),
        ("Retail", 942913.84), ("Partner", 822049.91), ("Marketplace", 106774.93)]


def test_two_base_metrics_by_region_on_postgres(pg_store):
    from living_reports.metrics.runner import run_query
    q = Query(metrics=["fraud_loss", "dealer_count"], dimensions=["dealers.region"],
              order=[["dealers.region", "asc"]])
    rows = run_query(q, "postgres_demo", pg_store).rows
    assert [(r, round(v, 2), n) for r, v, n in rows] == [
        ("East", 14121593.69, 1555), ("North", 11032331.04, 902),
        ("South", 17377050.48, 899), ("West", 10056682.92, 899)]
