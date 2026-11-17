"""The data brief: what the writer sees. Nothing is cut silently."""
from datetime import date

from living_reports.engine import Query, TimeRange
from living_reports.harness.brief import build_brief, counted_text, prompt_text
from living_reports.harness.claims import claims_for, readable
from living_reports.metrics.runner import to_facts
from tests.living_reports.test_claims import (
    HALVES,
    MONTH,
    WEST,
    region_case,
    result,
    series_case,
)


def test_counted_names_the_metric_filters_breakdown_period_and_rows():
    query = Query(metrics=["fraud_loss"], dimensions=["orders.fraud_type"], filters=WEST,
                  time=TimeRange(start=date(2024, 1, 1), end=date(2025, 12, 1), grain="month"))
    main = result(["orders.fraud_type", MONTH, "fraud_loss"], [], ["orders.fraud_type", MONTH])
    text = counted_text(query, main, {"dealers.region": "Region"}, (date(2024, 1, 1), date(2025, 11, 30)),
                        280_536, "orders")
    assert text == ("Fraud loss · Region is West · by Fraud type and month · Jan 2024 – Nov 2025"
                    " · 280,536 orders")


def test_a_long_series_rolls_up_and_says_so():
    query, results, facts = series_case()
    claims = claims_for(query, "q2", results, facts)
    brief = build_brief("Is West fraud loss rising?", query, "q2", results, claims, facts,
                        counted="Fraud loss · Region is West")
    assert "by half-year" in brief.table and "rolled up from 23 months" in brief.table
    assert "| Jul–Nov 2025 | $2.6M |" in brief.table and "Totals" not in brief.table
    assert len(brief.table.splitlines()) <= 14
    # The model reads each claim with its values written out.
    trend = next(c for c in brief.claims if ".trend." in c.id)
    assert trend.text.startswith("Fraud loss for West went up and down: $2.1M in 2024 H1")


def test_many_members_keep_the_top_and_an_other_row():
    rows = [(f"D{i}", float(1000 - i)) for i in range(30)]
    query, results, facts = region_case(rows)
    claims = claims_for(query, "q1", results, facts)
    brief = build_brief("Which dealers?", query, "q1", results, claims, facts)
    lines = brief.table.splitlines()
    assert "top 11 of 30" in lines[0]
    assert any(line.startswith("| Other (19) |") for line in lines)
    assert sum(line.startswith("|") for line in lines) <= 14  # header, rule, 12 rows


def test_a_breakdown_over_months_is_a_pivot():
    rows = []
    for y, months, total in HALVES:
        for m in months:
            rows += [["identity_theft", f"{y}-{m:02d}-01", 0.4 * total / len(months)],
                     ["bust_out", f"{y}-{m:02d}-01", 0.6 * total / len(months)]]
    query = Query(metrics=["fraud_loss"], dimensions=["orders.fraud_type"], filters=WEST,
                  time=TimeRange(start=date(2024, 1, 1), end=date(2025, 12, 1), grain="month"))
    main = result(["orders.fraud_type", MONTH, "fraud_loss"], rows, ["orders.fraud_type", MONTH])
    total = result(["fraud_loss"], [[9_300_000.0]], [])
    facts = [*to_facts(main, "q2"), *to_facts(total, "q2_total")]
    results = {"q2": main, "q2_total": total}
    brief = build_brief("q", query, "q2", results, claims_for(query, "q2", results, facts), facts)
    header = brief.table.splitlines()[2]
    assert header == "| Fraud type | 2024 H1 | 2024 H2 | 2025 H1 | Jul–Nov 2025 | Total |"
    assert "| Bust-out | $1.3M | $1.4M | $1.3M | $1.6M | $5.6M |" in brief.table


def test_prompt_text_lists_claims_and_context():
    query, results, facts = region_case()
    claims = claims_for(query, "q1", results, facts)
    brief = build_brief("Where?", query, "q1", results, claims, facts,
                        context=["Fraud loss was $48.8M in all."])
    text = prompt_text(brief)
    assert "Claims (each one is checked against the data):" in text
    assert f"- {claims.claims[0].id}: " in text
    assert "Earlier sections:\n- Fraud loss was $48.8M in all." in text


def _rate_by_region_and_month():
    from tests.living_reports.test_claims import RATE
    rows = []
    for y, months, _ in HALVES:
        for m in months:
            rows += [["South", f"{y}-{m:02d}-01", 0.031 + m / 1000], ["West", f"{y}-{m:02d}-01", 0.021 + m / 1000]]
    query = Query(metrics=["fraud_rate"], dimensions=["dealers.region"],
                  time=TimeRange(start=date(2024, 1, 1), end=date(2025, 12, 1), grain="month"))
    main = result(["dealers.region", MONTH, "fraud_rate"], rows, ["dealers.region", MONTH], meta=RATE)
    total = result(["fraud_rate"], [[0.027]], [], meta=RATE)
    return query, {"q3": main, "q3_total": total}, [*to_facts(main, "q3"), *to_facts(total, "q3_total")]


def test_a_rate_by_member_and_month_keeps_the_month_axis():
    query, results, facts = _rate_by_region_and_month()
    claims = claims_for(query, "q3", results, facts)
    table = build_brief("q", query, "q3", results, claims, facts).table
    lines = table.splitlines()
    assert "the last 6 of 23 months" in lines[0] and "not summed" in lines[0]
    assert lines[2] == "| Region | Jun 2025 | Jul 2025 | Aug 2025 | Sep 2025 | Oct 2025 | Nov 2025 |"
    assert "| South | 3.70% | 3.80% | 3.90% | 4.00% | 4.10% | 4.20% |" in lines
    assert "Other" not in table


def test_a_rate_gets_member_series_claims_from_its_own_points():
    query, results, facts = _rate_by_region_and_month()
    out = claims_for(query, "q3", results, facts)
    texts = [readable(c.text, out.facts(facts)) for c in out.claims]
    assert "Fraud rate for South rose in every month: 3.70% in Jun 2025, 3.80% in Jul 2025, 3.90% in Aug 2025, " \
           "4.00% in Sep 2025, 4.10% in Oct 2025, 4.20% in Nov 2025" in texts
    assert not any(c.kind in ("rank", "extreme", "share") for c in out.claims)  # sums of a rate mean nothing


def test_a_top_n_breakdown_over_months_folds_no_months():
    rows = [[r, f"2024-{m:02d}-01", v * m] for r, v in (("South", 10.0), ("East", 5.0)) for m in range(1, 13)]
    query = Query(metrics=["fraud_loss"], dimensions=["dealers.region"], time=TimeRange(grain="month"), limit=2)
    main = result(["dealers.region", MONTH, "fraud_loss"], rows, ["dealers.region", MONTH])
    facts = to_facts(main, "q1")
    claims = claims_for(query, "q1", {"q1": main}, facts)
    table = build_brief("q", query, "q1", {"q1": main}, claims, facts).table
    assert "| Region | Q1 2024 | Q2 2024 | Q3 2024 | Q4 2024 | Total |" in table.splitlines()
    assert "Other" not in table
