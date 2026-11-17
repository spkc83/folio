from datetime import date

import pytest
from pydantic import ValidationError

from living_reports.engine import Filter, FilterGroup, Having, OutOfScope, Query, QueryRejected
from living_reports.engine.query import TimeRange


def test_filter_group_needs_exactly_one_branch():
    with pytest.raises(ValidationError):
        FilterGroup(**{"and": [], "or": []})
    with pytest.raises(ValidationError):
        FilterGroup(**{"and": []})
    group = FilterGroup(**{"or": [Filter(member="orders.sales_channel", op="eq", values=["App"])]})
    assert group.any[0].member == "orders.sales_channel"


def test_time_range_rules():
    with pytest.raises(ValidationError):
        TimeRange()
    with pytest.raises(ValidationError):
        TimeRange(start=date(2025, 1, 1), end=date(2025, 1, 1))
    assert TimeRange(grain="month").grain == "month"


def test_query_rejects_duplicate_dimensions_and_bad_order_keys():
    with pytest.raises(ValidationError):
        Query(metrics=["fraud_loss"], dimensions=["orders.a", "orders.a"])
    with pytest.raises(ValidationError):
        Query(metrics=["fraud_loss"], order=[["profit", "desc"]])
    q = Query(metrics=["fraud_loss"], order=[["fraud_loss", "desc"]])
    assert q.order == [("fraud_loss", "desc")]


def test_dimensions_are_member_ids():
    with pytest.raises(ValidationError):
        Query(metrics=["fraud_loss"], dimensions=["month"])


def test_time_column_alias():
    q = Query(metrics=["fraud_loss"],
              time=TimeRange(field="orders.order_created_date", grain="month"))
    assert q.time_column == "orders.order_created_date:month"
    assert q.is_time_series
    q2 = Query(metrics=["fraud_loss"],
               time=TimeRange(field="orders.order_created_date", start=date(2025, 1, 1)))
    assert q2.time_column is None
    assert not q2.is_time_series


def test_order_may_name_the_time_column():
    q = Query(metrics=["fraud_loss"],
              time=TimeRange(field="orders.order_created_date", grain="month"),
              order=[["orders.order_created_date:month", "desc"]])
    assert q.order[0][0] == "orders.order_created_date:month"


def test_having_names_a_metric_of_the_query():
    h = Having(metric="fraud_loss", op="gt", values=[100])
    assert Query(metrics=["fraud_loss"], having=[h]).having[0].metric == "fraud_loss"
    with pytest.raises(ValidationError):
        Query(metrics=["fraud_loss"], having=[Having(metric="order_count", op="gt", values=[1])])
    with pytest.raises(ValidationError):
        Having(metric="fraud_loss", op="between", values=[1])


def test_spec_query_round_trips():
    spec = {
        "metrics": ["fraud_loss"],
        "dimensions": ["orders.sales_channel"],
        "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}],
        "time": {"field": "orders.order_created_date", "start": "2025-01-01",
                 "end": "2026-01-01", "grain": None},
        "order": [["fraud_loss", "desc"]],
        "limit": None,
    }
    q = Query.model_validate(spec)
    dumped = q.model_dump(mode="json", exclude_none=True)
    assert dumped["order"] == [["fraud_loss", "desc"]]
    assert dumped["time"] == {"field": "orders.order_created_date", "start": "2025-01-01",
                              "end": "2026-01-01"}
    assert Query.model_validate(dumped) == q


def test_nested_groups_round_trip():
    body = {"metrics": ["order_count"], "filters": [{"or": [
        {"member": "orders.sales_channel", "op": "eq", "values": ["Telesales"]},
        {"and": [{"member": "dealers.region", "op": "eq", "values": ["East"]}]},
    ]}]}
    q = Query.model_validate(body)
    assert isinstance(q.filters[0], FilterGroup)
    assert q.model_dump(mode="json", exclude_none=True, by_alias=True)["filters"] == body["filters"]


def test_errors_carry_reasons():
    e = OutOfScope("The model has no entry for: profit", ["profit"])
    assert e.reason.endswith("profit") and e.unknown == ["profit"]
    assert QueryRejected("Only one SELECT").reason == "Only one SELECT"


def test_sqlglot_is_installed():
    import sqlglot

    assert sqlglot.__version__.split(".")[0] == "30"
