from datetime import date

import pytest

from living_reports.engine import Filter, Having, OutOfScope, Query, TimeRange
from living_reports.engine.compile import compile
from living_reports.semantic.kinds import Ratio

from .conftest import add_metric, make_metric, run

BY_REGION = {"metrics": ["fraud_loss", "dealer_count"], "dimensions": ["dealers.region"]}


def test_two_bases_are_pre_aggregated(model, duck):
    c = compile(Query(**BY_REGION), model)
    assert run(c, duck) == [("East", 200.0, 2), ("West", 110.0, 2)]
    assert "FULL OUTER JOIN" in c.sql and "COALESCE" in c.sql
    assert c.plan.base_datasets == ["orders", "dealers"]
    assert c.plan.joins == {"orders": ["orders_dealer"], "dealers": []}
    assert c.plan.group_columns == ["dealers.region"]


def test_two_bases_with_a_filter_on_the_joined_dataset(model, duck):
    both = Filter(member="dealers.region", op="in", values=["East", "West"])
    c = compile(Query(**BY_REGION, filters=[both]), model)
    assert run(c, duck) == [("East", 200.0, 2), ("West", 110.0, 2)]
    west = Filter(member="dealers.region", op="eq", values=["West"])
    c = compile(Query(**BY_REGION, filters=[west]), model)
    assert run(c, duck) == [("West", 110.0, 2)]
    assert c.params == ["West", "West"]


def test_two_bases_without_dimensions_cross_join(model, duck):
    c = compile(Query(metrics=["fraud_loss", "dealer_count"]), model)
    assert "CROSS JOIN" in c.sql
    assert run(c, duck) == [(310.0, 4)]


def test_ratio_across_bases(model, duck):
    m = add_metric(model, make_metric(
        "loss_per_dealer", ratio=Ratio(numerator="fraud_loss", denominator="dealer_count")))
    c = compile(Query(metrics=["loss_per_dealer"]), m)
    assert run(c, duck) == [(77.5,)]
    c = compile(Query(metrics=["loss_per_dealer"], dimensions=["dealers.region"]), m)
    assert run(c, duck) == [("East", 100.0), ("West", 55.0)]


def test_month_grain(model, duck):
    q = Query(metrics=["fraud_loss"],
              filters=[Filter(member="dealers.region", op="eq", values=["East"])],
              time=TimeRange(field="orders.order_created_date", start=date(2025, 1, 1),
                             end=date(2026, 1, 1), grain="month"))
    c = compile(q, model)
    rows = run(c, duck)
    assert [(str(r[0])[:10], r[1]) for r in rows] == [
        ("2025-01-01", 100.0), ("2025-02-01", 50.0), ("2025-03-01", 30.0),
        ("2025-05-01", 0.0), ("2025-06-01", 0.0)]
    assert c.plan.group_columns == ["orders.order_created_date:month"]
    assert c.plan.time_column == "orders.order_created_date:month"
    assert "DATE_TRUNC('MONTH'" in c.sql


def test_grain_without_a_range(model, duck):
    c = compile(Query(metrics=["order_count"], time=TimeRange(grain="year")), model)
    assert [(r[0].year, r[1]) for r in run(c, duck)] == [(2024, 1), (2025, 7)]
    assert c.plan.time_column == "orders.order_created_date:year"


def test_grain_with_dimensions_orders_by_time_then_dimension(model, duck):
    c = compile(Query(metrics=["order_count"], dimensions=["orders.sales_channel"],
                      time=TimeRange(grain="year")), model)
    assert [(r[1].year, r[0], r[2]) for r in run(c, duck)] == [
        (2024, "App", 1), (2025, "App", 3), (2025, "Telesales", 1), (2025, "Web", 3)]
    assert c.plan.group_columns == ["orders.sales_channel", "orders.order_created_date:year"]


def test_order_by_the_time_column(model, duck):
    q = Query(metrics=["order_count"], time=TimeRange(grain="year"),
              order=[["orders.order_created_date:year", "desc"]])
    assert [r[0].year for r in run(compile(q, model), duck)] == [2025, 2024]


def test_week_grain_renders(model):
    c = compile(Query(metrics=["order_count"], time=TimeRange(grain="week")), model)
    assert "DATE_TRUNC('WEEK'" in c.sql


def test_grain_with_a_base_without_time_is_refused(model):
    with pytest.raises(OutOfScope, match="dealers has no time field"):
        compile(Query(**BY_REGION, time=TimeRange(grain="year")), model)


def test_having_applies_after_lowering(model, duck):
    c = compile(Query(**BY_REGION, having=[Having(metric="dealer_count", op="gte", values=[2])]),
                model)
    assert [r[0] for r in run(c, duck)] == ["East", "West"]
    c = compile(Query(**BY_REGION, having=[Having(metric="fraud_loss", op="gt", values=[150])]),
                model)
    assert run(c, duck) == [("East", 200.0, 2)]


def test_two_bases_with_a_limit_and_order(model, duck):
    c = compile(Query(**BY_REGION, order=[["dealers.region", "desc"]], limit=1), model)
    assert run(c, duck) == [("West", 110.0, 2)]
