"""The spec 5.3 acceptance values on the real demo data, through the join."""

import pytest

from living_reports.engine import Query
from living_reports.engine.compile import compile
from living_reports.sources import normalize, rows_of
from living_reports.sources.build import build_demo_source
from living_reports.sources.registry import source_for

pytestmark = pytest.mark.demo_data

SPEC = {
    "metrics": ["fraud_loss"],
    "dimensions": ["orders.sales_channel"],
    "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}],
    "time": {"field": "orders.order_created_date", "start": "2025-01-01", "end": "2026-01-01"},
    "order": [["fraud_loss", "desc"]],
}


@pytest.fixture
def model(real_store):
    return real_store.graph.model()


def _rows(q: Query, model, moment=2):
    build_demo_source(moment)
    source = source_for("duckdb_demo")
    c = compile(q, model, source.dialect)
    return [tuple(r) for r in rows_of(normalize(source.run(c.sql, c.params)))]


def test_spec_5_3_values(model):
    rows = _rows(Query.model_validate(SPEC), model)
    assert [(c, round(v, 2)) for c, v in rows] == [
        ("Telesales", 2807475.89), ("App", 1841271.48), ("Web", 1474301.94),
        ("Retail", 942913.84), ("Partner", 822049.91), ("Marketplace", 106774.93)]


def test_two_base_metrics_by_region(model):
    q = Query(metrics=["fraud_loss", "dealer_count"], dimensions=["dealers.region"],
              order=[["dealers.region", "asc"]])
    assert [(r, round(v, 2), n) for r, v, n in _rows(q, model)] == [
        ("East", 14121593.69, 1555), ("North", 11032331.04, 902),
        ("South", 17377050.48, 899), ("West", 10056682.92, 899)]


def test_total_matches_today(model):
    q = Query(metrics=["fraud_loss"])
    assert round(_rows(q, model, 2)[0][0], 2) == 52587658.13
    assert round(_rows(q, model, 1)[0][0], 2) == 48831770.69
    build_demo_source(2)
