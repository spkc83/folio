import pytest
from pydantic import ValidationError

from living_reports.metrics.runner import Fact, Result
from living_reports.spec.models import Bars, Prose, ReportSpec, Section, Tile
from living_reports.spec.validate import validate_spec

FACTS = {
    "t1": Fact(id="t1", label="Loss", value=1.0, formatted="$1.00", query_hash="h", source_id="duckdb_demo"),
    "e1": Fact(id="e1", label="Empty", value=None, formatted="No data", query_hash="h", source_id="duckdb_demo", state="empty"),
}
QUERIES = {"q1": Result(columns=["a"], rows=[], query_hash="h", source_id="duckdb_demo", row_count=0, sql="", params=[], metric_versions={}, metric_meta={}, dimensions=[])}


def spec(*blocks, title="A title", heading="Heading"):
    return ReportSpec(title=title, kicker="Fraud loss, 2024 to 2025", objective="why", assumptions=[],
                      sections=[Section(id="s", heading=heading, intent="i", blocks=list(blocks))])


def codes(s):
    return {i.code for i in validate_spec(s, FACTS, QUERIES)}


def test_digit_in_prose_is_rejected():
    assert codes(spec(Prose(text="Loss rose 38% this year."))) == {"raw_number"}


def test_token_passes():
    assert codes(spec(Prose(text="Loss reached {fact:t1}."))) == set()


def test_digit_in_title_is_rejected():
    assert codes(spec(Prose(text="ok"), title="Loss is 5 times higher")) == {"raw_number"}


def test_digit_in_heading_is_rejected():
    assert codes(spec(Prose(text="ok"), heading="Top 3 causes")) == {"raw_number"}


def test_system_written_kicker_may_have_dates():
    assert codes(spec(Prose(text="ok"))) == set()


def test_unknown_token():
    assert codes(spec(Prose(text="See {fact:zz}."))) == {"unknown_fact"}


def test_html_in_text_is_rejected():
    assert codes(spec(Prose(text="<script>alert()</script>"))) == {"markup"}


def test_less_than_as_a_word_is_allowed():
    assert codes(spec(Prose(text="Loss is < the target."))) == set()


def test_empty_fact_in_tile_is_flagged():
    assert codes(spec(Tile(fact_id="e1", label="Loss"))) == {"empty_fact"}


def test_unknown_fact_in_tile():
    assert codes(spec(Tile(fact_id="nope", label="Loss"))) == {"unknown_fact"}


def test_unknown_query_ref():
    assert codes(spec(Bars(query_ref="nope", title="t"))) == {"unknown_query"}


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError):
        Prose(text="x", html="<b>")


def test_blocks_round_trip_by_type():
    s = spec(Prose(text="ok"), Bars(query_ref="q1", title="t"))
    assert isinstance(ReportSpec.model_validate(s.model_dump()).sections[0].blocks[1], Bars)


def test_unknown_block_type_is_rejected():
    with pytest.raises(ValidationError):
        ReportSpec.model_validate({**spec(Prose(text="ok")).model_dump(), "sections": [{"id": "s", "heading": "h", "intent": "i", "blocks": [{"type": "iframe", "src": "x"}]}]})


def _f(id, formatted, label="L"):
    return Fact(id=id, label=label, value=1.0, formatted=formatted, query_hash="h", source_id="duckdb_demo")


def test_a_copied_value_becomes_its_token():
    from living_reports.spec.validate import tokenize_values
    facts = {"a": _f("a", "$15.6M"), "b": _f("b", "29.68%")}
    assert tokenize_values("App lost $15.6M, or 29.68% of the total.", facts) == "App lost {fact:a}, or {fact:b} of the total."


def test_a_value_shared_by_two_facts_stays_raw():
    from living_reports.spec.validate import tokenize_values
    facts = {"a": _f("a", "$10.0M", "Telesales"), "b": _f("b", "$10.0M", "Web")}
    assert tokenize_values("Telesales lost $10.0M.", facts) == "Telesales lost $10.0M."


def test_part_of_a_longer_number_is_not_matched():
    from living_reports.spec.validate import tokenize_values
    facts = {"a": _f("a", "$1.5M")}
    assert tokenize_values("It is $11.5M.", facts) == "It is $11.5M."


def test_an_unmatched_number_is_still_rejected():
    from living_reports.spec.validate import tokenize_values
    text = tokenize_values("Loss rose 38%.", {"a": _f("a", "$15.6M")})
    assert codes(spec(Prose(text=text))) == {"raw_number"}


def test_empty_facts_are_never_matched():
    from living_reports.spec.validate import tokenize_values
    facts = {"a": Fact(id="a", label="x", value=None, formatted="No data", query_hash="h", source_id="duckdb_demo", state="empty")}
    assert tokenize_values("No data here.", facts) == "No data here."


@pytest.mark.parametrize("text", [
    "Orders rose from January 2024 to December 2025.",
    "The total for 2024 is {fact:t1}.",
    "Data from 2024-01-01 to 2025-12-31.",
    "In Q3 2025 the loss was {fact:t1}.",
])
def test_dates_are_labels_not_values(text):
    assert codes(spec(Prose(text=text))) == set()


@pytest.mark.parametrize("text", ["Loss was 2024 dollars.", "About 1500 orders.", "It rose 2025%."])
def test_numbers_that_only_look_like_years_are_still_checked(text):
    assert "raw_number" in codes(spec(Prose(text=text)))


def _result(metric="fraud_loss", fmt="currency"):
    return Result(columns=[metric], rows=[[1.0]], query_hash="h", source_id="duckdb_demo", row_count=1, sql="",
                  params=[], metric_versions={metric: 1},
                  metric_meta={metric: {"label": "L", "format": fmt}}, dimensions=[])


def _facts():
    return {"q1_total.fraud_loss": _f("q1_total.fraud_loss", "$1.00")}


def test_tile_delta_needs_prev_query():
    tile = Tile(fact_id="q1_total.fraud_loss", label="Loss", delta="previous_period", delta_label="2025 vs 2024")
    queries = {"q1": _result(), "q1_cur": _result()}
    assert {i.code for i in validate_spec(spec(tile), _facts(), queries)} == {"no_previous_period"}
    queries["q1_prev"] = _result()
    assert validate_spec(spec(tile), _facts(), queries) == []
    no_cur = {k: v for k, v in queries.items() if k != "q1_cur"}
    assert {i.code for i in validate_spec(spec(tile), _facts(), no_cur)} == {"no_previous_period"}
    unlabeled = tile.model_copy(update={"delta_label": None})
    assert {i.code for i in validate_spec(spec(unlabeled), _facts(), queries)} == {"no_previous_period"}


def test_spark_ref_must_exist():
    tile = Tile(fact_id="q1_total.fraud_loss", label="Loss", spark_ref="q1_months")
    assert {i.code for i in validate_spec(spec(tile), _facts(), {"q1": _result()})} == {"unknown_query"}


def test_segmented_on_a_rate_is_rejected():
    from living_reports.spec.models import Segmented
    queries = {"q1": _result("fraud_rate", "percent")}
    block = Segmented(query_ref="q1", title="Rate")
    assert {i.code for i in validate_spec(spec(block), FACTS, queries)} == {"segmented_rate"}


def test_new_blocks_validate():
    from living_reports.spec.models import DeltaBars, Segmented, SmallMultiples
    queries = {"q1": _result(), "q1_prev_members": _result(), "q2": _result()}
    s = spec(SmallMultiples(query_ref="q2", title="t"), DeltaBars(query_ref="q1", against_ref="q1_prev_members", title="t"),
             Segmented(query_ref="q1", title="t"))
    assert validate_spec(s, FACTS, queries) == []
    assert isinstance(ReportSpec.model_validate(s.model_dump()).sections[0].blocks[1], DeltaBars)
    missing = spec(DeltaBars(query_ref="q1", against_ref="nope", title="t"))
    assert {i.code for i in validate_spec(missing, FACTS, queries)} == {"unknown_query"}


def _agg_case(text, agg, metric="order_value", share=False):
    from living_reports.metrics.runner import Binding

    fact = Fact(id="v1", label="Order value", value=1.0, formatted="$870.8M", query_hash="h",
                source_id="duckdb_demo", metric=metric,
                binding=Binding(ref="q1", metric=metric, share=share))
    meta = {} if agg is None else {metric: {"label": "Order value", "format": "currency", "agg": agg}}
    queries = {"q1": QUERIES["q1"].model_copy(update={"metric_meta": meta})}
    return {i.code for i in validate_spec(spec(Prose(text=text)), {"v1": fact}, queries)}


@pytest.mark.parametrize("text,agg", [
    ("The total number of orders is high, with an average order value of {fact:v1}.", "sum"),
    ("Mean order count was {fact:v1}.", "count"),
    ("The typical order is worth {fact:v1}.", "sum"),
    ("The total fraud rate is {fact:v1}.", "ratio"),
    ("Total spend per order is {fact:v1}.", "avg"),
])
def test_a_word_that_contradicts_the_aggregate_is_rejected(text, agg):
    assert _agg_case(text, agg) == {"wrong_aggregate"}


@pytest.mark.parametrize("text,agg", [
    ("The total order value is {fact:v1}.", "sum"),
    ("The average order value is {fact:v1}.", "avg"),
    ("Orders rose on average. Order value reached {fact:v1}.", "sum"),
    ("The fraud rate is {fact:v1}.", "ratio"),
    ("The average order value is {fact:v1}.", None),
])
def test_a_word_that_fits_the_aggregate_passes(text, agg):
    assert _agg_case(text, agg) == set()


def test_a_share_is_not_checked_for_its_aggregate():
    assert _agg_case("North has {fact:v1} of total order value.", "sum", share=True) == set()


def test_the_message_names_the_value_and_the_word():
    from living_reports.metrics.runner import Binding

    fact = Fact(id="v1", label="Order value", value=1.0, formatted="$870.8M", query_hash="h",
                source_id="duckdb_demo", metric="order_value",
                binding=Binding(ref="q1", metric="order_value"))
    queries = {"q1": QUERIES["q1"].model_copy(update={"metric_meta": {"order_value": {"agg": "sum"}}})}
    [issue] = validate_spec(spec(Prose(text="An average order value of {fact:v1}.")), {"v1": fact}, queries)
    assert issue.message == '{fact:v1} is a total, not an average. Remove "average".'


# --- claims and the word guard -------------------------------------------------------------

from living_reports.spec.models import Action, Claim  # noqa: E402

CLAIM = Claim(id="q1.value.fraud_loss", kind="value", text="Fraud loss was {fact:t1}",
              facts=["t1"], query_ref="q1", params={"subject": "Fraud loss", "fact": "t1"})


def guarded(*blocks, title="A title", heading="Heading", guard=True):
    s = spec(*blocks, title=title, heading=heading)
    s.claims = {CLAIM.id: CLAIM}
    return {i.code for i in validate_spec(s, FACTS, QUERIES, guard={"title", "s"} if guard else set())}


def test_a_known_claim_token_passes():
    assert guarded(Prose(text="In short, {claim:q1.value.fraud_loss}.")) == set()


def test_an_unknown_claim_token_is_rejected():
    assert guarded(Prose(text="{claim:q1.trend.x}.")) == {"unknown_claim"}


@pytest.mark.parametrize("text", [
    "Fraud loss was consistently increasing.", "The West is the highest region.",
    "Loss grew because of bust-out fraud.", "Loss is due to fraud rings.",
    "Telesales had more than the web.", "It rose.", "Losses are rising.",
    "The West is the top region.", "This channel has the most loss.",
])
def test_the_word_guard_rejects_claim_words_outside_tokens(text):
    assert guarded(Prose(text=text)) == {"claim_word"}


def test_the_word_guard_reads_only_text_outside_tokens():
    # The claim's own words ("highest") are code's, not the model's.
    assert guarded(Prose(text="Here is the picture: {claim:q1.value.fraud_loss}.")) == set()


def test_the_word_guard_checks_the_title_and_heading():
    assert guarded(Prose(text="ok"), title="Loss is rising") == {"claim_word"}
    assert guarded(Prose(text="ok"), heading="The top regions") == {"claim_word"}


@pytest.mark.parametrize("text", ["Most of this is plain.", "At least, here: {claim:q1.value.fraud_loss}.",
                                  "On top of that, {claim:q1.value.fraud_loss}."])
def test_bare_most_least_and_top_are_connecting_words(text):
    assert guarded(Prose(text=text)) == set()


def test_old_text_is_not_guarded():
    assert guarded(Prose(text="Loss was increasing."), guard=False) == set()


def test_an_action_needs_a_basis_of_known_claims():
    assert guarded(Action(text="Review the West.")) == {"action_without_basis"}
    assert guarded(Action(text="Review the West.", basis=["nope"])) == {"unknown_claim"}
    assert guarded(Action(text="Review the West.", basis=[CLAIM.id])) == set()


def test_claim_token_in_a_title_is_rejected():
    assert "token_in_title" in guarded(Prose(text="ok"), title="{claim:q1.value.fraud_loss}")


def test_old_report_without_claims_still_validates():
    old = ReportSpec.model_validate({
        "title": "T", "objective": "why",
        "sections": [{"id": "s1", "heading": "H", "intent": "i",
                      "blocks": [{"type": "prose", "text": "Loss was {fact:t1}."},
                                 {"type": "action", "text": "Act."}]}]})
    assert old.claims == {} and old.sections[0].narration == "model"
    assert validate_spec(old, FACTS, QUERIES) == []


@pytest.mark.parametrize("text", ["Increase the review of telesales orders.", "Raise the limit for the top dealers.",
                                  "Reduce exposure where loss is highest."])
def test_an_action_may_use_trend_and_rank_verbs(text):
    assert guarded(Action(text=text, basis=[CLAIM.id])) == set()


def test_an_action_may_not_state_a_cause():
    assert guarded(Action(text="Act because of fraud rings.", basis=[CLAIM.id])) == {"claim_word"}


def test_higher_than_and_lower_than_are_comparisons():
    assert guarded(Prose(text="West was higher than East.")) == {"claim_word"}
    assert guarded(Prose(text="West was lower than East.")) == {"claim_word"}


def test_a_cited_claim_whose_fact_is_missing_is_flagged():
    s = spec(Prose(text="{claim:q1.gone}."))
    s.claims = {"q1.gone": CLAIM.model_copy(update={"id": "q1.gone", "text": "Loss was {fact:nope}"})}
    assert "unknown_fact" in {i.code for i in validate_spec(s, FACTS, QUERIES)}
