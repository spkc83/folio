from datetime import date, datetime

import pytest

from living_reports.engine import Filter, Query, TimeRange
from living_reports.engine.expressions import parse_expression
from living_reports.metrics.runner import run_query
from living_reports.reports import explore
from living_reports.reports.explore import (
    DIFFERENT_FILTERS,
    MID_MONTH,
    TOP_WITH_OTHERS,
    ScopedQuery,
    build_cube,
    cube_or_reason,
    describe,
    scoped_query,
)
from living_reports.semantic.kinds import Measure, MetricBindingPayload, Ratio

CHANNEL, REGION, TYPE = "orders.sales_channel", "dealers.region", "orders.fraud_type"
DEFS = {"q1": {"metrics": ["fraud_loss", "fraud_rate", "order_count"], "dimensions": [CHANNEL]}}
EAST = [{"member": REGION, "op": "eq", "values": ["East"]}]
WEST = [{"member": REGION, "op": "eq", "values": ["West"]}]


@pytest.fixture(autouse=True)
def _at_moment_2(request):
    """These tests read all the demo data, as the "s2" snapshot did."""
    if "store" in request.fixturenames:
        from living_reports.sources.build import build_demo_source
        request.getfixturevalue("store")
        build_demo_source(2)


def _column(cube, name):
    index = cube.columns.index(name)
    return [row[index] for row in cube.rows]


def _total(cube, name):
    return sum(v or 0 for v in _column(cube, name))


def _put_metric(store, metric_id, **form):
    payload = MetricBindingPayload(name=metric_id, label=metric_id.replace("_", " ").capitalize(),
                                   format="currency", **form)
    store.graph.put(metric_id, "MetricBinding", payload, reason="test", valid_from=datetime(2024, 1, 1))


def test_cube_lowers_metrics_to_components(store):
    cube = build_cube(DEFS, "duckdb_demo", store)
    assert cube is not None
    assert cube.dimensions == [CHANNEL, "month"]
    assert cube.labels == {CHANNEL: "Sales channel", "month": "Month"}
    assert cube.vocabulary[TYPE] == "Fraud type" and "orders.order_created_date" not in cube.vocabulary
    rate, loss, orders = cube.metrics["fraud_rate"], cube.metrics["fraud_loss"], cube.metrics["order_count"]
    assert rate.kind == "ratio" and rate.den is not None and rate.format == "percent"
    assert loss.kind == "sum" and loss.den is None and loss.label == "Fraud loss"
    assert orders.kind == "count"
    direct = run_query(Query(metrics=["fraud_loss", "fraud_rate"]), "duckdb_demo", store).rows[0]
    assert _total(cube, loss.num) == pytest.approx(direct[0])
    assert _total(cube, rate.num) / _total(cube, rate.den) == pytest.approx(direct[1], abs=1e-9)
    assert all(isinstance(m, str) and len(m) == 10 for m in _column(cube, "month"))
    assert cube.span == (None, None) and len(cube.query_hash) == 64


def test_a_ratio_metric_matches_the_server(store):
    _put_metric(store, "loss_per_order", ratio=Ratio(numerator="fraud_loss", denominator="order_count"))
    cube = build_cube({"q1": {"metrics": ["loss_per_order"], "dimensions": [REGION]}}, "duckdb_demo", store)
    agg = cube.metrics["loss_per_order"]
    assert (agg.kind, agg.num, agg.den) == ("ratio", "fraud_loss", "order_count")
    direct = run_query(Query(metrics=["loss_per_order"]), "duckdb_demo", store).rows[0][0]
    assert _total(cube, agg.num) / _total(cube, agg.den) == pytest.approx(direct)
    east = run_query(Query(metrics=["loss_per_order"], filters=[Filter(**EAST[0])]), "duckdb_demo", store).rows[0][0]
    rows = [r for r in cube.rows if r[cube.columns.index(REGION)] == "East"]
    num = sum(r[cube.columns.index("fraud_loss")] for r in rows)
    den = sum(r[cube.columns.index("order_count")] for r in rows)
    assert num / den == pytest.approx(east)


def test_count_distinct_and_expressions_are_not_reaggregable(store):
    model = store.graph.model()
    _put_metric(store, "loss_thousands",
                expression=parse_expression("sum(orders.loss_amount / 1000)", "orders", model))
    _put_metric(store, "channels", measure=Measure(agg="count_distinct", field=CHANNEL))
    cube = build_cube({"q1": {"metrics": ["loss_thousands", "channels"], "dimensions": [REGION]}}, "duckdb_demo", store)
    assert cube.metrics["loss_thousands"].kind == "other" and cube.metrics["channels"].kind == "other"


def test_a_metric_the_engine_cannot_run_by_month_has_no_cube(store):
    # dealer_count counts dealers, and the dealers table has no time field.
    cube, reason = cube_or_reason({"q1": {"metrics": ["dealer_count"], "dimensions": [REGION]}}, "duckdb_demo", store)
    assert cube is None and "no time field" in reason


def test_a_base_filter_metric_matches_the_server(store):
    _put_metric(store, "theft_loss", measure=Measure(agg="sum", field="orders.loss_amount",
                                                     filter={"member": TYPE, "op": "eq", "values": ["identity_theft"]}))
    cube = build_cube({"q1": {"metrics": ["theft_loss", "fraud_loss"], "dimensions": [REGION]}}, "duckdb_demo", store)
    direct = run_query(Query(metrics=["theft_loss"]), "duckdb_demo", store).rows[0][0]
    assert _total(cube, "theft_loss") == pytest.approx(direct)
    assert _total(cube, "theft_loss") < _total(cube, "fraud_loss")


def test_cube_carries_filter_dimensions(store):
    defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL], "filters": EAST}}
    assert build_cube(defs, "duckdb_demo", store).dimensions == [CHANNEL, REGION, "month"]


def test_cube_keeps_the_report_filters(store):
    defs = {"q1": {"metrics": ["fraud_loss", "order_count"], "dimensions": [CHANNEL], "filters": EAST}}
    cube = build_cube(defs, "duckdb_demo", store)
    assert set(_column(cube, REGION)) == {"East"}
    east = run_query(Query(metrics=["fraud_loss", "order_count"], filters=[Filter(**EAST[0])]), "duckdb_demo", store).rows[0]
    assert _total(cube, "fraud_loss") == pytest.approx(east[0])
    assert _total(cube, "order_count") == east[1]


def test_cube_filter_values_are_parameters(store):
    hostile = "East' OR '1'='1"
    defs = {"q1": {"metrics": ["order_count"], "dimensions": [CHANNEL],
                   "filters": [{"member": REGION, "op": "in", "values": [hostile, "West"]}]}}
    cube = build_cube(defs, "duckdb_demo", store)
    assert set(_column(cube, REGION)) == {"West"}


def test_cube_with_disjoint_filters_has_no_cube(store):
    defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL], "filters": EAST},
            "q2": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL], "filters": WEST}}
    assert cube_or_reason(defs, "duckdb_demo", store) == (None, DIFFERENT_FILTERS)
    same = {"q1": defs["q1"], "q2": {**defs["q1"], "dimensions": [], "time": {"grain": "month"}}}
    assert set(_column(cube_or_reason(same, "duckdb_demo", store)[0], REGION)) == {"East"}


def test_cube_covers_the_widest_query_only(store):
    # q1 covers every region, and q2 (a pinned East section) is inside q1, so the cube is q1's rows.
    defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL]},
            "q2": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL], "filters": EAST,
                   "time": {"start": "2025-01-01", "end": "2025-04-01"}}}
    cube = build_cube(defs, "duckdb_demo", store)
    assert set(_column(cube, REGION)) == {"East", "West"} and cube.span == (None, None)
    apart = {"q1": {"metrics": ["fraud_loss"], "time": {"start": "2025-07-01", "end": "2025-10-01"}},
             "q2": {"metrics": ["fraud_loss"], "time": {"start": "2025-01-01", "end": "2025-04-01"}}}
    assert cube_or_reason(apart, "duckdb_demo", store) == (None, DIFFERENT_FILTERS)


def test_bounded_cube_carries_one_previous_period(store):
    defs = {"q1": {"metrics": ["fraud_loss"], "filters": EAST,
                   "time": {"start": "2025-07-01", "end": "2025-10-01"}}}
    cube = build_cube(defs, "duckdb_demo", store)
    assert cube.span == ("2025-04-01", "2025-10-01")
    assert min(_column(cube, "month")) == "2025-04-01" and max(_column(cube, "month")) == "2025-09-01"
    assert set(_column(cube, REGION)) == {"East"}
    before = run_query(Query(metrics=["fraud_loss"], filters=[Filter(**EAST[0])],
                             time=TimeRange(start=date(2025, 4, 1), end=date(2025, 10, 1))), "duckdb_demo", store).rows[0][0]
    assert _total(cube, "fraud_loss") == pytest.approx(before)


def test_top_cube_holds_exactly_the_shown_members(store):
    q = {"metrics": ["order_count"], "dimensions": [CHANNEL], "limit": 3}
    cube, reason = cube_or_reason({"q1": q}, "duckdb_demo", store)
    shown = run_query(Query.model_validate(q), "duckdb_demo", store).rows
    assert reason is None and set(_column(cube, CHANNEL)) == {r[0] for r in shown}
    totals: dict = {}
    for channel, orders in zip(_column(cube, CHANNEL), _column(cube, "order_count")):
        totals[channel] = totals.get(channel, 0) + orders
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    assert [[k, v] for k, v in ranked] == shown


def test_a_top_query_beside_other_queries_has_no_cube(store):
    defs = {"q1": {"metrics": ["order_count"], "dimensions": [CHANNEL], "limit": 3},
            "q2": {"metrics": ["order_count"], "dimensions": [REGION]}}
    assert cube_or_reason(defs, "duckdb_demo", store) == (None, TOP_WITH_OTHERS)


def test_cube_refuses_a_mid_month_bound(store):
    defs = {"q1": {"metrics": ["fraud_loss"], "time": {"start": "2025-07-15", "end": "2025-10-01"}}}
    assert cube_or_reason(defs, "duckdb_demo", store) == (None, MID_MONTH)
    assert cube_or_reason({}, "duckdb_demo", store) == (None, None)


def test_cube_over_the_row_cap_is_none(store, monkeypatch):
    monkeypatch.setattr(explore, "CUBE_MAX_ROWS", 10)
    assert cube_or_reason(DEFS, "duckdb_demo", store) == (None, explore.TOO_LARGE)


def test_an_old_stored_definition_still_gets_a_cube(store):
    old = {"q1": {"metrics": ["fraud_loss"], "dimensions": ["sales_channel"]}}
    assert build_cube(old, "duckdb_demo", store).dimensions == [CHANNEL, "month"]


def test_scoped_query_adds_member_and_range(store):
    model = store.graph.model()
    defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL]}}
    sq = ScopedQuery(ref="q1", filters=[Filter(**EAST[0])], range=("2025-07-01", "2025-11-01"), by=TYPE,
                     member={CHANNEL: "Telesales"})
    assert scoped_query(defs, sq, model) == Query(
        metrics=["fraud_loss"], dimensions=[TYPE],
        filters=[Filter(**EAST[0]), Filter(member=CHANNEL, op="eq", values=["Telesales"])],
        time=TimeRange(start=date(2025, 7, 1), end=date(2025, 12, 1)),
    )


def test_scoped_query_keeps_inside_the_def_bounds(store):
    model = store.graph.model()
    defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL], "limit": 3,
                   "time": {"start": "2025-01-01", "end": "2025-10-01"}}}
    q = scoped_query(defs, ScopedQuery(ref="q1", range=("2024-11-01", "2025-11-01")), model)
    assert (q.time.start, q.time.end, q.limit) == (date(2025, 1, 1), date(2025, 10, 1), 3)


def test_scoped_query_refuses_unknown_refs_and_null_members(store):
    model = store.graph.model()
    with pytest.raises(KeyError):
        scoped_query({}, ScopedQuery(ref="q9"), model)
    defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL]}}
    with pytest.raises(ValueError, match="A member with no value cannot be queried"):
        scoped_query(defs, ScopedQuery(ref="q1", member={TYPE: None}), model)


def test_scoped_query_drops_a_dimension_the_scope_fixes(store):
    model = store.graph.model()
    defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL]}}
    sq = ScopedQuery(ref="q1", filters=[Filter(member=CHANNEL, op="eq", values=["Telesales"])])
    assert scoped_query(defs, sq, model).dimensions == []
    by_region = ScopedQuery(ref="q1", filters=[Filter(**EAST[0])])
    assert scoped_query(defs, by_region, model).dimensions == [CHANNEL]
    drill = ScopedQuery(ref="q1", by=TYPE, member={CHANNEL: "Telesales"})
    assert scoped_query(defs, drill, model).dimensions == [TYPE]


def test_describe_reads_as_words(store):
    model = store.graph.model()
    defs = {"q1": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL]}}
    sq = ScopedQuery(ref="q1", range=("2025-07-01", "2025-11-01"), by=TYPE, member={CHANNEL: "Telesales"})
    labels = {"fraud_loss": "Fraud loss", TYPE: "Fraud type", CHANNEL: "Sales channel", REGION: "Region"}
    assert describe(defs, sq, labels, model) == "Fraud loss by fraud type · Sales channel = Telesales · Jul 2025 – Nov 2025"
    plain = ScopedQuery(ref="q1", filters=[Filter(**EAST[0])])
    assert describe(defs, plain, labels, model) == "Fraud loss by sales channel · Region = East"


def test_a_rebuilt_source_rebuilds_the_cube(store):
    """The cube's identity holds the source's data time: new data gives a new cube."""
    from living_reports.sources.build import build_demo_source
    from living_reports.sources.registry import source_for
    defs = {"q1": {"metrics": ["order_count"], "dimensions": [CHANNEL]}}
    before = build_cube(defs, "duckdb_demo", store)
    assert build_cube(defs, "duckdb_demo", store).query_hash == before.query_hash
    build_demo_source(1)
    after = build_cube(defs, "duckdb_demo", store)
    assert after.query_hash != before.query_hash
    assert _total(after, "order_count") == 2800 and _total(before, "order_count") == 3000
    assert after.source_id == "duckdb_demo" and after.data_time == source_for("duckdb_demo").data_time()


def test_a_top_query_with_no_rows_has_no_cube(store):
    q = {"metrics": ["order_count"], "dimensions": [CHANNEL], "limit": 3,
         "time": {"start": "2023-01-01", "end": "2023-04-01"}}
    assert cube_or_reason({"q1": q}, "duckdb_demo", store) == (None, explore.NO_MEMBERS)


def test_cube_carries_members_named_inside_a_filter_group(store):
    group = {"or": [{"member": REGION, "op": "eq", "values": ["East"]},
                    {"member": TYPE, "op": "eq", "values": ["none"]}]}
    cube = build_cube({"q1": {"metrics": ["order_count"], "dimensions": [CHANNEL], "filters": [group]}}, "duckdb_demo", store)
    assert cube.dimensions == [CHANNEL, REGION, TYPE, "month"]


def test_a_drill_on_a_monthly_query_breaks_down_by_one_member(store):
    model = store.graph.model()
    defs = {"q1": {"metrics": ["fraud_loss"], "time": {"grain": "month", "start": "2025-01-01", "end": "2025-07-01"}}}
    q = scoped_query(defs, ScopedQuery(ref="q1", by=TYPE, range=("2025-03-01", "2025-04-01")), model)
    assert q.dimensions == [TYPE] and not q.is_time_series
    assert (q.time.start, q.time.end) == (date(2025, 3, 1), date(2025, 5, 1))


def test_a_scope_range_outside_the_query_dates_says_so(store):
    model = store.graph.model()
    defs = {"q1": {"metrics": ["fraud_loss"], "time": {"start": "2025-01-01", "end": "2025-04-01"}}}
    with pytest.raises(ValueError, match=r"^The range is outside the report's dates\.$"):
        scoped_query(defs, ScopedQuery(ref="q1", range=("2025-07-01", "2025-09-01")), model)
