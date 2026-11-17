"""Claims that do not hide a story: flat steps in a trend, a peak month, and the full history of a rate.

The examples are the planted stories of the demo data that the claims missed or stated in a
misleading way (the insight eval).
"""
from datetime import date

from living_reports.engine import Query, TimeRange
from living_reports.harness.claims import check, claims_for, readable
from living_reports.metrics.runner import to_facts

from .test_claims import MONTH, by_kind, result
from .test_claims_direction import RATE

# Digital goods fraud rate by month, Jun to Nov 2025: a card-testing burst in August.
BURST = [0.0048, 0.0027, 0.0564, 0.0028, 0.0029, 0.0007]
# Gross margin by month, Jun to Nov 2025: one fall, then four flat steps.
MARGIN = [0.3406, 0.3097, 0.3116, 0.3101, 0.3090, 0.3110]


def monthly(values, meta=RATE, metric="fraud_rate", start=6):
    query = Query(metrics=[metric],
                  time=TimeRange(start=date(2025, start, 1), end=date(2025, start + len(values), 1),
                                 grain="month"))
    months = [f"2025-{m:02d}-01" for m in range(start, start + len(values))]
    main = result([MONTH, metric], [[m, v] for m, v in zip(months, values)], [MONTH], meta=meta)
    facts = to_facts(main, "q1")
    out = claims_for(query, "q1", {"q1": main}, facts)
    return out, out.facts(facts)


def texts(out, facts, kind):
    return [readable(c.text, facts) for c in by_kind(out.claims, kind)]


def test_a_trend_names_its_flat_steps():
    meta = {"gross_margin": {"label": "Gross margin", "format": "percent", "agg": "ratio",
                             "definition": "", "better": "higher"}}
    out, facts = monthly(MARGIN, meta, "gross_margin")
    assert texts(out, facts, "trend") == [
        "Gross margin fell in 1 of 5 months and was flat (within 2%) in 4: 34.06% in Jun 2025, "
        "30.97% in Jul 2025, 31.16% in Aug 2025, 31.01% in Sep 2025, 30.90% in Oct 2025, "
        "31.10% in Nov 2025"]


def test_a_month_far_above_the_others_is_a_peak():
    out, facts = monthly(BURST)
    assert texts(out, facts, "peak") == [
        "Fraud rate peaked at 5.64% in Aug 2025, 20.1× the median month (0.28%)"]
    peak = by_kind(out.claims, "peak")[0]
    assert out.claims[0] is peak  # it leads the section, before the change across the window
    assert check(peak, facts)


def test_a_peak_needs_twice_the_median_and_a_single_top_month():
    out, _ = monthly([0.010, 0.011, 0.019, 0.010, 0.012, 0.011])  # 1.73x the median
    assert by_kind(out.claims, "peak") == []
    out, _ = monthly([0.010, 0.030, 0.011, 0.030, 0.010, 0.012])  # two top months
    assert by_kind(out.claims, "peak") == []
    out, _ = monthly([0.010, 0.040, 0.011, 0.010, 0.012])  # five months are too few
    assert by_kind(out.claims, "peak") == []


def test_a_peak_goes_stale_when_the_peak_month_changes():
    out, facts = monthly(BURST)
    peak = by_kind(out.claims, "peak")[0]
    month = next(f for f in peak.facts if facts[f].value == 0.0564)
    lower = facts | {month: facts[month].model_copy(update={"value": 0.004, "formatted": "0.40%"})}
    assert not check(peak, lower)


QUARTER = "orders.order_created_date:quarter"
# Junior Engineering attrition: 1% a month until a reorganization in March 2025, then 3%.
REORG = [0.01] * 14 + [0.03] * 9  # Jan 2024 .. Nov 2025


def reorg_case(rollup=True):
    query = Query(metrics=["fraud_rate"],
                  time=TimeRange(start=date(2024, 1, 1), end=date(2025, 12, 1), grain="month"))
    months = [f"{2024 + i // 12}-{i % 12 + 1:02d}-01" for i in range(len(REORG))]
    main = result([MONTH, "fraud_rate"], [[m, v] for m, v in zip(months, REORG)], [MONTH], meta=RATE)
    results = {"q1": main}
    if rollup:  # the engine computes each quarter's rate from its rows, as a query at that grain
        quarters = {}
        for m, v in zip(months, REORG):
            q = f"{m[:4]}-{(int(m[5:7]) - 1) // 3 * 3 + 1:02d}-01"
            quarters.setdefault(q, []).append(v)
        rows = [[q, sum(vs) / len(vs)] for q, vs in quarters.items()]
        results["q1_rollup"] = result([QUARTER, "fraud_rate"], rows, [QUARTER], meta=RATE)
    facts = to_facts(main, "q1")
    out = claims_for(query, "q1", results, facts)
    return out, out.facts(facts)


def test_a_rate_over_many_months_rolls_up_to_quarters_and_keeps_the_reorganization():
    out, facts = reorg_case()
    changes = texts(out, facts, "change")
    assert ("Fraud rate rose sharply from 1.00% in Q3 2024 to 3.00% in Q3 2025 (+200.0%), "
            "a change for the worse") in changes
    assert any(t.endswith("Oct–Nov 2025, a partial period") or "Oct–Nov 2025, a partial period" in t
               for t in changes)
    assert all(check(c, facts) for c in out.claims)


def test_without_the_rollup_a_rate_shows_only_its_last_months():
    out, facts = reorg_case(rollup=False)
    assert texts(out, facts, "change") == [
        "Fraud rate was flat (within 2%) from 3.00% in Jun 2025 to 3.00% in Nov 2025"]


def test_a_peak_falls_back_on_both_sides():
    out, _ = monthly([0.003, 0.003, 0.004, 0.003, 0.004, 0.012])  # a last-month high: a rise
    assert by_kind(out.claims, "peak") == []
    out, _ = monthly([0.003, 0.003, 0.010, 0.009, 0.003, 0.003])  # two high months: a level
    assert by_kind(out.claims, "peak") == []


YEAR = "orders.order_created_date:year"
REGION = "dealers.region"


def test_a_breakdown_by_year_knows_its_last_year_is_partial():
    query = Query(metrics=["fraud_loss"], dimensions=[REGION],
                  time=TimeRange(start=date(2024, 1, 1), end=date(2025, 12, 1), grain="year"))
    main = result([REGION, YEAR, "fraud_loss"],
                  [["West", "2024-01-01", 1_000_000], ["West", "2025-01-01", 1_600_000],
                   ["East", "2024-01-01", 2_000_000], ["East", "2025-01-01", 2_000_000]],
                  [REGION, YEAR])
    months = [f"{2024 + i // 12}-{i % 12 + 1:02d}-01" for i in range(23)]
    by_month = result([MONTH, "fraud_loss"], [[m, 6_600_000 / 23] for m in months], [MONTH])
    total = result([YEAR, "fraud_loss"], [["2024-01-01", 3_000_000], ["2025-01-01", 3_600_000]], [YEAR])
    results = {"q1": main, "q1_total": total, "q1_months": by_month}
    facts = [*to_facts(main, "q1"), *to_facts(total, "q1_total")]
    out = claims_for(query, "q1", results, facts)
    assert any(readable(c.text, out.facts(facts)).startswith(
        "Fraud loss for West rose from $1.0M in 2024 to $1.6M in Jan–Nov 2025, a partial period")
        for c in by_kind(out.claims, "change"))
