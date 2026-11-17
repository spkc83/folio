"""The engine reads ``TimeRange.end`` as exclusive. Every producer of an end sends the day
after the last day it means, and every display shows the inclusive last day."""
from datetime import date

import pytest

from living_reports.engine import Filter, Query, TimeRange
from living_reports.harness.dates import exclusive_end, span_text
from living_reports.harness.entities import Found, Period
from living_reports.harness.reconcile import fill
from living_reports.harness.router import route
from living_reports.harness.selection import Selection, with_selection
from living_reports.metrics.runner import run_query
from living_reports.semantic.demo import seed_demo
from living_reports.store import get_store

# The demo source at moment 1 holds the test orders from 2024-01-01 to 2025-11-30.
LAST = date(2025, 11, 30)
WEST = [{"member": "dealers.region", "op": "eq", "values": ["West"]}]


@pytest.fixture
def seeded(store, orders_file):
    store = get_store()
    seed_demo(store.graph)
    return store


def draft(**kw):
    return {"objective": "status", "query": None, "clarify": None, "assumptions": [],
            "out_of_scope": None, **kw}


def _total(query: Query, store) -> float:
    result = run_query(query, "duckdb_demo", store)
    index = result.columns.index("order_count")
    return sum(row[index] for row in result.rows)


def test_the_full_range_keeps_the_last_day_of_data(fake_llm, seeded):
    """A model copies the data's last day into ``end``. The last day must stay in the total."""
    time = {"start": "2024-01-01", "end": LAST.isoformat(), "grain": "month"}
    fake_llm.push(draft(query={"metrics": ["order_count"], "dimensions": [], "filters": WEST,
                               "time": time}))
    routed = route("West orders by month", seeded, "duckdb_demo", "t")
    assert routed.query.time.end == date(2025, 12, 1)
    last_day = Query(metrics=["order_count"], filters=[Filter(**WEST[0])],
                     time=TimeRange(start=LAST, end=date(2025, 12, 1)))
    assert _total(last_day, seeded) > 0  # the test means something only with orders on it
    everything = Query(metrics=["order_count"], filters=[Filter(**WEST[0])])
    monthly = _total(routed.query, seeded)
    assert monthly == _total(everything, seeded)
    total = routed.query.model_copy(update={"time": routed.query.time.model_copy(update={"grain": None})})
    assert _total(total, seeded) == monthly


def test_a_month_end_from_the_model_means_through_that_day(fake_llm, seeded):
    time = {"start": "2024-01-01", "end": "2024-12-31"}
    fake_llm.push(draft(query={"metrics": ["order_count"], "dimensions": [], "time": time}))
    routed = route("Orders in 2024", seeded, "duckdb_demo", "t")
    assert routed.query.time.end == date(2025, 1, 1)


def test_an_exclusive_end_from_the_model_is_kept(fake_llm, seeded):
    time = {"start": "2024-01-01", "end": "2025-01-01"}
    fake_llm.push(draft(query={"metrics": ["order_count"], "dimensions": [], "time": time}))
    routed = route("Orders in 2024", seeded, "duckdb_demo", "t")
    assert routed.query.time.end == date(2025, 1, 1)


def test_a_clarify_option_gets_the_same_end(fake_llm, seeded):
    loss = {"metrics": ["fraud_loss"], "dimensions": [],
            "time": {"start": "2024-01-01", "end": LAST.isoformat()}}
    clarify = {"question": "Which?", "options": [{"label": "A", "query": loss},
                                                 {"label": "B", "query": loss}]}
    fake_llm.push(draft(clarify=clarify))
    routed = route("Loss", seeded, "duckdb_demo", "t")
    assert routed.query.time.end == date(2025, 12, 1)


@pytest.mark.parametrize("end, last, expected", [
    (date(2025, 11, 30), date(2025, 11, 30), date(2025, 12, 1)),  # the data's last day
    (date(2024, 12, 31), date(2025, 11, 30), date(2025, 1, 1)),   # a month's last day
    (date(2024, 2, 29), None, date(2024, 3, 1)),                  # a leap February
    (date(2025, 1, 1), date(2025, 11, 30), date(2025, 1, 1)),     # already exclusive
    (date(2025, 6, 15), date(2025, 11, 30), date(2025, 6, 15)),   # a day inside a month
    (None, date(2025, 11, 30), None),
])
def test_exclusive_end(end, last, expected):
    assert exclusive_end(end, last) == expected


@pytest.mark.parametrize("start, end, text", [
    (date(2024, 1, 1), date(2025, 12, 1), "Jan 2024 – Nov 2025"),
    (date(2024, 1, 1), date(2025, 11, 30), "Jan 2024 – Nov 2025"),  # an old inclusive end
    (date(2025, 1, 1), date(2026, 1, 1), "2025"),
    (date(2025, 3, 1), date(2025, 4, 1), "Mar 2025"),
    (None, None, "All dates"),
    (date(2025, 3, 1), None, "From Mar 2025"),
    (None, date(2025, 4, 1), "Through Mar 2025"),
])
def test_span_text_shows_the_inclusive_last_month(start, end, text):
    assert span_text(start, end) == text


def test_reconcile_fills_a_period_after_an_inclusive_end():
    period = Period(date(2024, 1, 1), date(2025, 1, 1), "2024")
    query = Query(metrics=["order_count"], time=TimeRange(end=date(2024, 12, 31)))
    filled, notes = fill(query, Found(period=period), _Vocab())
    assert (filled.time.start, filled.time.end) == (date(2024, 1, 1), date(2025, 1, 1))


def test_a_selected_range_ends_after_its_last_day():
    selection = Selection(range=(date(2024, 1, 1), date(2025, 11, 30)))
    query = with_selection(Query(metrics=["order_count"]), selection, None)
    assert query.time.end == date(2025, 12, 1)


class _Vocab:
    def label(self, member: str) -> str:
        return member


@pytest.mark.demo_data
def test_real_west_loss_keeps_the_last_day(real_store):
    """On the demo data, a range that ends on the data's last day keeps that day's orders."""
    from living_reports.harness.router import _date_range
    from living_reports.semantic.projections import vocabulary_of

    model = real_store.graph.model()
    first, last = _date_range("duckdb_demo", model, vocabulary_of(model))
    west = [Filter(member="dealers.region", op="eq", values=["West"])]

    def loss(time: TimeRange | None) -> float:
        result = run_query(Query(metrics=["fraud_loss"], filters=west, time=time),
                           "duckdb_demo", real_store)
        index = result.columns.index("fraud_loss")
        return sum(row[index] or 0 for row in result.rows)

    end = exclusive_end(last, last)
    everything = loss(None)
    assert loss(TimeRange(start=first, end=end, grain="month")) == pytest.approx(everything)
    assert loss(TimeRange(start=first, end=end)) == pytest.approx(everything)
    assert loss(TimeRange(start=last, end=end)) > 0


def test_a_stored_inclusive_end_reruns_through_that_day(seeded):
    """Old reports stored the data's last day (2025-11-30) as an exclusive end."""
    from living_reports.engine.query import stored_query

    model = seeded.graph.model()
    old = {"metrics": ["order_count"], "dimensions": [], "filters": WEST,
           "time": {"start": "2024-01-01", "end": LAST.isoformat(), "grain": "month"}}
    query = stored_query(old, model)
    assert query.time.end == date(2025, 12, 1)
    assert _total(query, seeded) == _total(Query(metrics=["order_count"], filters=[Filter(**WEST[0])]), seeded)
    kept = {**old, "time": {"start": "2024-01-01", "end": "2025-01-01"}}
    assert stored_query(kept, model).time.end == date(2025, 1, 1)


@pytest.mark.demo_data
def test_real_stored_report_end_reruns_to_the_full_west_total(real_store):
    """A report stored with the data's last month-end as ``end`` reruns with that day."""
    from datetime import timedelta

    from living_reports.engine.query import stored_query
    from living_reports.harness.router import _date_range
    from living_reports.semantic.projections import vocabulary_of

    model = real_store.graph.model()
    first, last = _date_range("duckdb_demo", model, vocabulary_of(model))
    assert (last + timedelta(days=1)).day == 1  # the demo data ends on a month's last day
    old = {"metrics": ["fraud_loss"], "dimensions": ["dealers.region", "orders.fraud_type"],
           "filters": WEST, "having": [], "order": [],
           "time": {"start": first.isoformat(), "end": last.isoformat(), "grain": "month"}}

    def loss(query: Query) -> float:
        result = run_query(query, "duckdb_demo", real_store)
        index = result.columns.index("fraud_loss")
        return sum(row[index] or 0 for row in result.rows)

    fresh = Query(metrics=["fraud_loss"], filters=[Filter(**WEST[0])])
    assert loss(stored_query(old, model)) == pytest.approx(loss(fresh))
