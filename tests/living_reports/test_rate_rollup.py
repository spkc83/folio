"""A rate over time gets a quarterly series from the engine, so its claims see more than the last months."""
import pytest

from living_reports.engine import Query, TimeRange
from living_reports.harness.builder import AskContext, run_section
from living_reports.reports.store import ReportStore


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def _grain(result):
    return next(c for c in result.columns if ":" in c).rsplit(":", 1)[-1]


def test_a_rate_by_month_gets_a_quarterly_rollup(ctx):
    results, _ = run_section(Query(metrics=["fraud_rate"], time=TimeRange(grain="month")), "q1", ctx)
    assert _grain(results["q1_rollup"]) == "quarter"
    assert "q1_member_rollup" not in results


def test_a_rate_by_member_and_month_gets_a_member_rollup(ctx):
    query = Query(metrics=["fraud_rate"], dimensions=["dealers.region"], time=TimeRange(grain="month"))
    results, _ = run_section(query, "q1", ctx)
    assert _grain(results["q1_rollup"]) == "quarter"
    assert _grain(results["q1_member_rollup"]) == "quarter"
    assert "dealers.region" in results["q1_member_rollup"].columns


def test_an_amount_needs_no_rollup(ctx):
    results, _ = run_section(Query(metrics=["fraud_loss"], time=TimeRange(grain="month")), "q1", ctx)
    assert "q1_rollup" not in results


def test_a_rate_with_no_time_grain_gets_a_quarterly_rollup_for_its_series(ctx):
    results, _ = run_section(Query(metrics=["fraud_rate"], dimensions=["dealers.region"]), "q1", ctx)
    assert _grain(results["q1_rollup"]) == "quarter"
    assert "q1_member_rollup" not in results  # a breakdown with no time has no member series
