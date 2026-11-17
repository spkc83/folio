"""Claims: code computes, checks, and renders each statement that a report makes."""
from datetime import date

import pytest

from living_reports.engine import Query, TimeRange
from living_reports.harness.claims import check, claims_for, readable, recheck, value_label
from living_reports.metrics.runner import Result, share_facts, to_facts

LOSS = {"fraud_loss": {"label": "Fraud loss", "format": "currency", "agg": "sum",
                       "definition": "Total loss on orders confirmed as fraud."}}
RATE = {"fraud_rate": {"label": "Fraud rate", "format": "percent", "agg": "avg", "definition": ""}}
MONTH = "orders.order_created_date:month"
WEST = [{"member": "dealers.region", "op": "eq", "values": ["West"]}]
# West fraud loss by half-year (the real report): $2.11M, $2.34M, $2.22M, $2.63M (Jul-Nov 2025).
HALVES = [(2024, range(1, 7), 2_110_000), (2024, range(7, 13), 2_340_000),
          (2025, range(1, 7), 2_220_000), (2025, range(7, 12), 2_630_000)]


def result(columns, rows, dims, meta=LOSS, hash_="h1"):
    return Result(columns=columns, rows=rows, query_hash=hash_, source_id="duckdb_demo",
                  row_count=len(rows), sql="", params=[], metric_versions={m: 1 for m in meta},
                  metric_meta=meta, dimensions=dims,
                  labels={"dealers.region": "Region", "orders.fraud_type": "Fraud type",
                          MONTH: "Month", **{m: v["label"] for m, v in meta.items()}})


def west_months():
    rows = [[f"{y}-{m:02d}-01", total / len(months)] for y, months, total in HALVES for m in months]
    return result([MONTH, "fraud_loss"], rows, [MONTH])


def series_case():
    query = Query(metrics=["fraud_loss"], filters=WEST,
                  time=TimeRange(start=date(2024, 1, 1), end=date(2025, 12, 1), grain="month"))
    main = west_months()
    return query, {"q2": main}, to_facts(main, "q2")


REGIONS = [("South", 15_840_000), ("East", 12_500_000), ("North", 11_200_000), ("West", 9_290_000)]


def region_case(rows=REGIONS):
    query = Query(metrics=["fraud_loss"], dimensions=["dealers.region"])
    main = result(["dealers.region", "fraud_loss"], [list(r) for r in rows], ["dealers.region"])
    total = result(["fraud_loss"], [[sum(v for _, v in rows)]], [])
    facts = [*to_facts(main, "q1"), *to_facts(total, "q1_total"),
             *share_facts(main, "fraud_loss", "q1", total)]
    return query, {"q1": main, "q1_total": total}, facts


def by_kind(claims, kind):
    return [c for c in claims if c.kind == kind]


def test_a_trend_that_went_up_and_down_is_not_called_consistent():
    query, results, facts = series_case()
    out = claims_for(query, "q2", results, facts)
    (trend,) = by_kind(out.claims, "trend")
    text = readable(trend.text, out.facts(facts))
    # The trend compares full half-years only: Jul–Nov 2025 is a partial period.
    assert text == "Fraud loss for West went up and down: $2.1M in 2024 H1, $2.3M in 2024 H2, $2.2M in 2025 H1"
    assert "every" not in text and "consistent" not in text


def test_the_series_rolls_up_to_half_years_with_derived_facts():
    query, results, facts = series_case()
    out = claims_for(query, "q2", results, facts)
    derived = {f.id: f for f in out.derived}
    first = derived["q2.derived.fraud_loss.half.West.2024-H1"]
    assert first.value == pytest.approx(2_110_000) and first.query_hash == "h1"
    assert first.basis == "sum of 6 monthly values"
    last = derived["q2.derived.fraud_loss.half.West.2025-H2"]
    assert last.basis == "sum of 5 monthly values (a partial half-year: Jul–Nov 2025)"
    assert last.label == "Fraud loss for West · Jul–Nov 2025"


def test_a_change_compares_full_periods_and_names_a_partial_one():
    query, results, facts = series_case()
    out = claims_for(query, "q2", results, facts)
    known = out.facts(facts)
    full, partial = sorted(by_kind(out.claims, "change"), key=lambda c: c.id.endswith(".partial"))
    assert readable(full.text, known) == "Fraud loss for West rose from $2.1M in 2024 H1 to $2.2M in 2025 H1 (+5.2%)"
    # +24.6% is past the "sharply" line, but "sharply" never spans a partial period.
    assert readable(partial.text, known) == (
        "Fraud loss for West rose from $2.1M in 2024 H1 to $2.6M in Jul–Nov 2025, a partial period (+24.6%)")
    assert partial.period == ("2024-01-01", "2025-11-30")
    assert [(p.label, p.partial) for p in partial.evidence.series] == [
        ("2024 H1", False), ("2024 H2", False), ("2025 H1", False), ("Jul–Nov 2025", True)]


def test_every_candidate_checks_and_a_changed_value_fails_the_check():
    query, results, facts = series_case()
    out = claims_for(query, "q2", results, facts)
    known = out.facts(facts)
    assert out.claims and all(check(c, known) for c in out.claims)
    (trend,) = by_kind(out.claims, "trend")
    bumped = dict(known)
    fid = "q2.derived.fraud_loss.half.West.2024-H1"
    bumped[fid] = known[fid].model_copy(update={"value": 3_000_000.0})
    assert not check(trend, bumped)  # now it fell in 2 of 3
    del bumped[fid]
    assert not check(trend, bumped)


def test_rank_names_the_lowest_member_and_the_named_segment_comes_first():
    query, results, facts = region_case()
    out = claims_for(query, "q1", results, facts, question="How is the West doing?")
    known = out.facts(facts)
    first = out.claims[0]
    assert first.segment == {"dealers.region": "West"}
    texts = [readable(c.text, known) for c in out.claims]
    assert texts[0] == "West had the lowest fraud loss of the 4 regions ($9.3M, 19.03% of $48.8M)"
    assert "South had the highest fraud loss of the 4 regions ($15.8M, 32.44% of $48.8M)" in texts
    # The extreme already says it: no rank claim repeats "the lowest" for West.
    assert sum("West had the lowest" in t for t in texts) == 1


def test_a_count_has_the_most_and_the_fewest():
    meta = {"leavers": {"label": "Leavers", "format": "count", "agg": "sum", "definition": ""}}
    rows = [["Engineering", 412.0], ["Sales", 260.0], ["Finance", 95.0]]
    query = Query(metrics=["leavers"], dimensions=["dealers.region"])
    main = result(["dealers.region", "leavers"], rows, ["dealers.region"], meta=meta)
    total = result(["leavers"], [[767.0]], [], meta=meta)
    facts = [*to_facts(main, "q1"), *to_facts(total, "q1_total"),
             *share_facts(main, "leavers", "q1", total)]
    out = claims_for(query, "q1", {"q1": main, "q1_total": total}, facts)
    texts = [readable(c.text, out.facts(facts)) for c in out.claims]
    assert "Engineering had the most leavers of the 3 regions (412, 53.72% of 767)" in texts
    assert "Finance had the fewest leavers of the 3 regions (95, 12.39% of 767)" in texts
    assert "Sales ranked 2nd of the 3 regions by leavers (260, 33.90% of the total)" in texts
    assert not any("highest" in t or "lowest" in t for t in texts)


def test_compare_uses_a_derived_ratio():
    query, results, facts = region_case()
    out = claims_for(query, "q1", results, facts, question="West?")
    (compare,) = by_kind(out.claims, "compare")
    known = out.facts(facts)
    assert readable(compare.text, known) == "South's fraud loss ($15.8M) was 1.7× West's ($9.3M)"
    rows = compare.evidence.rows
    assert [r.label for r in rows if r.highlight] == ["South", "West"]


def test_evidence_rows_and_counted():
    query, results, facts = region_case()
    out = claims_for(query, "q1", results, facts, counted="Fraud loss · by Region")
    rank = next(c for c in out.claims if c.kind == "rank")
    assert rank.evidence.counted == "Fraud loss · by Region" and rank.evidence.by == "Region"
    assert rank.evidence.definition == "Total loss on orders confirmed as fraud."
    assert [r.label for r in rank.evidence.rows] == ["South", "East", "North", "West"]


def test_a_tie_is_not_called_highest():
    query, results, facts = region_case([("South", 5.0), ("East", 5.0), ("West", 1.0)])
    out = claims_for(query, "q1", results, facts)
    texts = [readable(c.text, out.facts(facts)) for c in out.claims]
    assert not any("highest" in t or "largest" in t for t in texts)
    assert any("tied for 1st" in t for t in texts)


def test_flat_within_two_percent():
    query = Query(metrics=["fraud_loss"], time=TimeRange(grain="month"))
    main = result([MONTH, "fraud_loss"], [["2024-01-01", 100.0], ["2024-02-01", 101.0]], [MONTH])
    out = claims_for(query, "q1", {"q1": main}, to_facts(main, "q1"))
    (change,) = by_kind(out.claims, "change")
    assert "was flat (within 2%)" in readable(change.text, out.facts(to_facts(main, "q1")))


def test_member_series_of_a_breakdown_over_months():
    """West by fraud type and month: member totals, the extreme, and the section trend."""
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
    out = claims_for(query, "q2", {"q2": main, "q2_total": total}, facts)
    known = out.facts(facts)
    texts = [readable(c.text, known) for c in out.claims]
    assert "Bust-out had the highest fraud loss of the 2 fraud types for West ($5.6M, 60.00% of $9.3M)" in texts
    assert "Fraud loss for West went up and down: $2.1M in 2024 H1, $2.3M in 2024 H2, $2.2M in 2025 H1" in texts
    assert all(check(c, known) for c in out.claims)


def test_at_most_forty_candidates():
    rows = [(f"D{i}", float(1000 - i)) for i in range(120)]
    query, results, facts = region_case(rows)
    assert len(claims_for(query, "q1", results, facts).claims) <= 40


def test_a_top_n_query_makes_no_rank_claims():
    query, results, facts = region_case()
    query = query.model_copy(update={"limit": 3})
    kinds = {c.kind for c in claims_for(query, "q1", results, facts).claims}
    assert not kinds & {"rank", "extreme", "compare"}


def test_value_label_fallback():
    assert value_label("orders.fraud_type", "bust_out") == "Bust-out"
    assert value_label(None, "2024-01-01") == "Jan 2024"
    assert value_label("dealers.region", "West") == "West"


def test_about_the_same_keeps_the_ratio_it_checks():
    query, results, facts = region_case([("South", 101.0), ("West", 100.0)])
    out = claims_for(query, "q1", results, facts, question="West?")
    (compare,) = by_kind(out.claims, "compare")
    known = out.facts(facts)
    assert "about the same as West's" in readable(compare.text, known)
    assert compare.params["ratio"] in known and check(compare, known)


def test_a_short_last_period_is_left_out_of_a_change():
    """One month of 2026 next to six-month half-years is not a fair step."""
    rows = [[f"{y}-{m:02d}-01", 100.0] for y in (2024, 2025) for m in range(1, 13)] + [["2026-01-01", 1.0]]
    main = result([MONTH, "fraud_loss"], rows, [MONTH])
    query = Query(metrics=["fraud_loss"], time=TimeRange(grain="month"))
    out = claims_for(query, "q1", {"q1": main}, to_facts(main, "q1"))
    (change,) = by_kind(out.claims, "change")
    assert change.params["last_label"] == "2025 H2"
    assert [p.label for p in out.total_series][-1] == "Jan 2026"  # the table still shows it


def test_value_label_uses_the_display_labels():
    from living_reports.harness.claims import value_label

    assert value_label("orders.fraud_type", "dealer_collusion") == "Dealer collusion"
    assert value_label("orders.fraud_type", "none") == "No fraud"
    assert value_label("month", "2024-01-01") == "Jan 2024"
    assert value_label(None, "bust_out") == "Bust out"  # no member: no table, plain words


def test_a_two_dimension_rank_names_combinations():
    pairs = [("West", "identity_theft", 4.0), ("West", "bust_out", 3.0), ("East", "identity_theft", 2.0),
             ("East", "bust_out", 1.0), ("South", "identity_theft", 0.5)]
    query = Query(metrics=["fraud_loss"], dimensions=["dealers.region", "orders.fraud_type"])
    main = result(["dealers.region", "orders.fraud_type", "fraud_loss"], [list(r) for r in pairs],
                  ["dealers.region", "orders.fraud_type"])
    total = result(["fraud_loss"], [[10.5]], [])
    facts = [*to_facts(main, "q1"), *to_facts(total, "q1_total"),
             *share_facts(main, "fraud_loss", "q1", total)]
    out = claims_for(query, "q1", {"q1": main, "q1_total": total}, facts)
    texts = [readable(c.text, out.facts(facts)) for c in out.claims]
    assert any("ranked 2nd of the 5 combinations of Region and Fraud type by fraud loss" in t for t in texts)
    assert any("had the highest fraud loss of the 5 combinations of Region and Fraud type" in t for t in texts)
    assert not any("region and Fraud types" in t for t in texts)


def test_quarter_labels_read_q_then_year():
    rows = [[f"2024-{m:02d}-01", 100.0 + m] for m in range(1, 13)]
    main = result([MONTH, "fraud_loss"], rows, [MONTH])
    query = Query(metrics=["fraud_loss"], time=TimeRange(grain="month"))
    out = claims_for(query, "q1", {"q1": main}, to_facts(main, "q1"))
    assert [p.label for p in out.total_series] == ["Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024"]


def test_a_change_between_equal_rounded_values_names_the_rounding():
    main = result([MONTH, "fraud_loss"], [["2024-01-01", 1_000_000.0], ["2024-02-01", 1_024_000.0]], [MONTH])
    query = Query(metrics=["fraud_loss"], time=TimeRange(grain="month"))
    facts = to_facts(main, "q1")
    out = claims_for(query, "q1", {"q1": main}, facts)
    (change,) = by_kind(out.claims, "change")
    assert readable(change.text, out.facts(facts)) == (
        "Fraud loss rose +2.4%, from Jan 2024 to Feb 2024 (both round to $1.0M)")
    assert check(change, out.facts(facts))


def test_y_nouns_pluralize_to_ies():
    rows = [("Toys", 5.0), ("Food", 3.0), ("Books", 1.0)]
    query = Query(metrics=["fraud_loss"], dimensions=["orders.category"])
    main = result(["orders.category", "fraud_loss"], [list(r) for r in rows], ["orders.category"])
    main = main.model_copy(update={"labels": {**main.labels, "orders.category": "Category"}})
    total = result(["fraud_loss"], [[9.0]], [])
    facts = [*to_facts(main, "q1"), *to_facts(total, "q1_total"), *share_facts(main, "fraud_loss", "q1", total)]
    texts = [readable(c.text, claims_for(query, "q1", {"q1": main, "q1_total": total}, facts).facts(facts))
             for c in claims_for(query, "q1", {"q1": main, "q1_total": total}, facts).claims]
    assert any("of the 3 categories" in t for t in texts) and not any("categorys" in t for t in texts)


def test_without_a_named_member_the_leader_comes_first():
    query, results, facts = region_case()
    out = claims_for(query, "q1", results, facts, question="Where is the loss?")
    assert out.claims[0].id == "q1.extreme.max.fraud_loss"


def test_recheck_keeps_the_facts_of_a_stale_claim():
    from living_reports.harness.claims import keep_stale_facts
    from living_reports.spec.models import Prose, ReportSpec, Section
    query, results, facts = series_case()
    out = claims_for(query, "q2", results, facts)
    trend = by_kind(out.claims, "trend")[0]
    old = out.facts(facts)
    spec = ReportSpec(title="t", objective="why", claims={trend.id: trend}, sections=[
        Section(id="s2", heading="h", intent="i", blocks=[Prose(text=f"{{claim:{trend.id}}}.")])])
    new = {k: v for k, v in old.items() if ".derived." not in k}  # a grain change: no half-year facts
    rechecked = recheck(spec, [], {"q2"}, new)
    assert rechecked.sections[0].stale_claims == [trend.id]
    kept = keep_stale_facts(rechecked, new, old)
    assert all(f in kept for f in trend.facts)


def test_a_label_that_ends_in_s_takes_an_apostrophe_only():
    query, results, facts = region_case([("Electronics", 4_800_000.0), ("Home", 2_900_000.0)])
    out = claims_for(query, "q1", results, facts)
    (compare,) = by_kind(out.claims, "compare")
    assert readable(compare.text, out.facts(facts)).startswith("Electronics' fraud loss ($4.8M) was 1.7× Home's")


def test_a_named_member_tied_at_the_top_keeps_its_rank():
    query, results, facts = region_case([("South", 5.0), ("East", 5.0), ("West", 1.0)])
    out = claims_for(query, "q1", results, facts, question="How is the South doing?")
    texts = [readable(c.text, out.facts(facts)) for c in out.claims]
    assert any(t.startswith("South was tied for 1st of the 3 regions") for t in texts)
