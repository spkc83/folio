"""Business knowledge in the semantic model: the better direction of a metric and its target.

Code renders "a change for the worse" and "below the target of 35.00%" only from a metric's
``better`` and ``target`` fields and checks them like every other claim.
"""
import pytest
from pydantic import ValidationError

from living_reports.engine import Query
from living_reports.harness.claims import check, claims_for, readable
from living_reports.metrics.runner import to_facts
from living_reports.semantic.demo import seed_demo
from living_reports.semantic.kinds import Measure, MetricBindingPayload, Ratio
from living_reports.semantic.ossie import document_to_changes, export_model, parse_document
from living_reports.semantic.kinds import SourcePayload
from living_reports.spec.models import Prose, ReportSpec, Section
from living_reports.spec.validate import validate_spec

from .test_claims import LOSS, by_kind, region_case, result, series_case

LOSS_DOWN = {"fraud_loss": {**LOSS["fraud_loss"], "better": "lower"}}
RATE = {"fraud_rate": {"label": "Fraud rate", "format": "percent", "agg": "avg", "definition": "",
                       "better": "lower", "target": 0.03}}
CHANNELS = [("Telesales", 0.035), ("Partner", 0.030), ("Web", 0.0246), ("Retail", 0.0236)]


def _with_meta(case, meta):
    query, results, facts = case
    for r in results.values():
        r.metric_meta = meta
    return query, results, facts


def _texts(out, facts, kind):
    return [readable(c.text, out.facts(facts)) for c in by_kind(out.claims, kind)]


def channel_case(total=0.0261):
    query = Query(metrics=["fraud_rate"], dimensions=["orders.sales_channel"])
    main = result(["orders.sales_channel", "fraud_rate"], [list(r) for r in CHANNELS],
                  ["orders.sales_channel"], meta=RATE)
    main.labels["orders.sales_channel"] = "Sales channel"
    whole = result(["fraud_rate"], [[total]], [], meta=RATE)
    return query, {"q1": main, "q1_total": whole}, [*to_facts(main, "q1"), *to_facts(whole, "q1_total")]


# --- direction -----------------------------------------------------------------------------


def test_a_rise_in_a_lower_is_better_metric_is_a_change_for_the_worse():
    query, results, facts = _with_meta(series_case(), LOSS_DOWN)
    out = claims_for(query, "q2", results, facts)
    texts = _texts(out, facts, "change")
    assert texts and all(t.endswith(", a change for the worse") for t in texts)


def test_without_a_direction_a_change_has_no_judgment():
    query, results, facts = series_case()
    out = claims_for(query, "q2", results, facts)
    assert not any("worse" in t or "better" in t for t in _texts(out, facts, "change"))


def test_an_extreme_of_a_rate_reads_as_plain_english():
    """"Telesales had the highest fraud rate of the 4 sales channels (3.50%)", with no
    ", the worst result" after it: read out loud, that tail sounds like a robot."""
    query, results, facts = channel_case()
    out = claims_for(query, "q1", results, facts, question="How is Retail doing?")
    extremes = _texts(out, facts, "extreme")
    assert "Telesales had the highest fraud rate of the 4 sales channels (3.50%)" in extremes
    assert "Retail had the lowest fraud rate of the 4 sales channels (2.36%)" in extremes
    assert not any("result" in t for t in extremes)


def test_a_total_has_no_best_or_worst_member():
    # A region with more loss is mostly a bigger region: size is not a result.
    query, results, facts = _with_meta(region_case(), LOSS_DOWN)
    out = claims_for(query, "q1", results, facts, question="How is West doing?")
    texts = _texts(out, facts, "rank") + _texts(out, facts, "extreme")
    assert texts and not any("best" in t or "worst" in t for t in texts)


def test_a_plural_metric_label_takes_were():
    meta = {"order_count": {"label": "Orders", "format": "count", "agg": "count", "definition": ""}}
    main = result(["order_count"], [[44782]], [], meta=meta)
    query = Query(metrics=["order_count"])
    facts = to_facts(main, "q1")
    out = claims_for(query, "q1", {"q1": main}, facts)
    assert _texts(out, facts, "value") == ["Orders were 44,782"]


def test_a_singular_metric_label_takes_was():
    query, results, facts = channel_case()
    out = claims_for(query, "q1", results, facts)
    assert "Fraud rate was 2.61%" in _texts(out, facts, "value")


def test_a_direction_claim_fails_its_check_when_the_direction_changes():
    query, results, facts = _with_meta(series_case(), LOSS_DOWN)
    out = claims_for(query, "q2", results, facts)
    change = by_kind(out.claims, "change")[0]
    all_facts = out.facts(facts)
    assert check(change, all_facts)
    flipped = change.model_copy(update={"params": {**change.params, "better": "higher"}})
    assert not check(flipped, all_facts)


# --- target --------------------------------------------------------------------------------


def test_a_member_that_misses_the_target_gets_a_target_claim():
    query, results, facts = channel_case()
    out = claims_for(query, "q1", results, facts)
    texts = _texts(out, facts, "target")
    assert "Fraud rate for Telesales was 3.50%, above the target of 3.00%" in texts
    # A member that meets the target is not listed unless the question names it.
    assert not any("Web" in t or "Retail" in t for t in texts)
    # A missed target is the main finding: it leads the section.
    assert readable(out.claims[0].text, out.facts(facts)).startswith("Fraud rate for Telesales was 3.50%, above")


def test_the_section_total_names_the_whole_breakdown_and_the_target():
    """"Total fraud rate across all sales channels was 2.61%, below the target of 3.00%"."""
    query, results, facts = channel_case()
    out = claims_for(query, "q1", results, facts)
    assert ("Total fraud rate across all sales channels was 2.61%, below the target of 3.00%"
            in _texts(out, facts, "target"))


def test_a_total_with_no_breakdown_keeps_its_plain_subject():
    whole = result(["fraud_rate"], [[0.0261]], [], meta=RATE)
    facts = to_facts(whole, "q1")
    out = claims_for(Query(metrics=["fraud_rate"]), "q1", {"q1": whole}, facts)
    assert _texts(out, facts, "target") == ["Fraud rate was 2.61%, below the target of 3.00%"]


def test_a_named_member_gets_a_target_claim_first_even_when_it_meets_the_target():
    query, results, facts = channel_case()
    out = claims_for(query, "q1", results, facts, question="Is Web on target?")
    first = out.claims[0]
    assert first.kind == "target"
    assert readable(first.text, out.facts(facts)) == "Fraud rate for Web was 2.46%, below the target of 3.00%"


def test_the_target_is_a_derived_fact_and_a_changed_value_fails_the_check():
    query, results, facts = channel_case()
    out = claims_for(query, "q1", results, facts)
    claim = next(c for c in by_kind(out.claims, "target") if "Telesales" in c.id)
    all_facts = out.facts(facts)
    target = all_facts["q1.derived.fraud_rate.target"]
    assert target.value == 0.03 and target.basis == "the target in the metric definition"
    assert target.id in claim.facts
    assert check(claim, all_facts)
    member = claim.params["fact"]
    moved = {**all_facts, member: all_facts[member].model_copy(update={"value": 0.02})}
    assert not check(claim, moved)


def test_a_sum_metric_gets_no_target_claim():
    meta = {"fraud_loss": {**LOSS["fraud_loss"], "better": "lower", "target": 1_000_000}}
    query, results, facts = _with_meta(region_case(), meta)
    assert by_kind(claims_for(query, "q1", results, facts).claims, "target") == []


# --- the semantic model --------------------------------------------------------------------


def _metric(**kw):
    return MetricBindingPayload(name="fraud_rate", label="Fraud rate", format="percent",
                                measure=Measure(agg="avg", field="orders.is_fraud"), **kw)


def test_a_target_needs_a_better_direction():
    with pytest.raises(ValidationError, match="better"):
        _metric(target=0.03)
    assert _metric(better="lower", target=0.03).target == 0.03


def test_a_target_on_a_sum_or_a_count_is_rejected():
    with pytest.raises(ValidationError, match="rate or an average"):
        MetricBindingPayload(name="fraud_loss", label="Fraud loss", format="currency", better="lower",
                             target=1.0, measure=Measure(agg="sum", field="orders.loss_amount"))
    ok = MetricBindingPayload(name="r", label="R", format="percent", better="lower", target=0.5,
                              ratio=Ratio(numerator="a", denominator="b"))
    assert ok.target == 0.5


def test_the_demo_model_knows_the_better_direction_and_the_fraud_rate_target(fg):
    seed_demo(fg)
    m = fg.model()
    assert m.metric("fraud_loss").better == "lower"
    assert (m.metric("fraud_rate").better, m.metric("fraud_rate").target) == ("lower", 0.03)
    assert m.metric("order_value").better == "higher"
    assert m.metric("dealer_count").better is None


def test_a_demo_graph_seeded_before_directions_gets_them_once(fg):
    # A graph written before metrics had a direction: no demo metric has one.
    seed_demo(fg)
    for metric_id in ["fraud_loss", "fraud_rate", "order_count", "order_value"]:
        old = fg.slice().node(metric_id).payload
        fg.put(metric_id, "MetricBinding",
               MetricBindingPayload(**{**old, "better": None, "target": None}), reason="old seed")
    seed_demo(fg)
    assert fg.model().metric("fraud_rate").target == 0.03
    before = fg.version()
    seed_demo(fg)
    assert fg.version() == before


def test_ossie_round_trip_keeps_better_and_target(fg):
    seed_demo(fg)
    model = fg.model()
    changes, dropped = document_to_changes(parse_document(export_model(model)),
                                           SourcePayload(name=model.source))
    rate = next(c.payload for c in changes if c.id == "fraud_rate")
    assert (rate["better"], rate["target"]) == ("lower", 0.03)


# --- the word guard ------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Telesales missed the target.", "The West is worse.", "Loss worsened.",
    "Fraud improved in the South.", "Retail is on target.",
])
def test_the_word_guard_rejects_direction_and_target_words(text):
    spec = ReportSpec(title="Fraud", objective="status",
                      sections=[Section(id="s1", heading="Fraud", intent="q", blocks=[Prose(text=text)])])
    assert {i.code for i in validate_spec(spec, {}, {}, {"s1"})} == {"claim_word"}


def test_a_miss_that_rounds_to_the_target_says_so():
    # Partner: 3.0016% shows as 3.00%, so "above the target of 3.00%" needs the note.
    query, results, facts = channel_case()
    results["q1"].rows[1][1] = 0.030016
    facts = [*to_facts(results["q1"], "q1"), *to_facts(results["q1_total"], "q1_total")]
    out = claims_for(query, "q1", results, facts)
    assert ("Fraud rate for Partner was 3.00%, above the target of 3.00% (both round to 3.00%)"
            in _texts(out, facts, "target"))
