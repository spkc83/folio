"""Word tokens: the writer writes its own sentence, and code writes each finding word.

"North had the {word:<id>} return rate at {fact:<id>}" renders "North had the highest return
rate at 6.08%". The model never types "highest", "below", or "rose": code takes each from a
claim, so the word is true for the data. Code also checks that the sentence holds the claim's
own value, names the claim's member and no other member, and does not negate the word.
"""
import pytest

from living_reports.harness.builder import AskContext, answer, free_words, ste_problems, style_problems
from living_reports.harness.claims import cited_claims, claims_for, render_claims, word_of
from living_reports.reports.store import ReportStore
from living_reports.spec.models import Claim
from living_reports.spec.validate import check_text, check_word_tokens, check_words
from tests.living_reports.test_claims import by_kind, region_case, series_case

# --- the word of each claim kind ------------------------------------------------------------


def _claims(case):
    query, results, facts = case
    out = claims_for(query, next(iter(results)), results, facts)
    return out.claims, out.facts(facts)


def test_an_extreme_claim_gives_highest_or_lowest():
    claims, _ = _claims(region_case())
    words = {c.params["which"]: c.word for c in by_kind(claims, "extreme")}
    assert words == {"max": "highest", "min": "lowest"}


def test_a_middle_rank_gives_its_place():
    claims, _ = _claims(region_case())
    (east,) = [c for c in by_kind(claims, "rank") if c.params["member"] == "East"]
    assert east.word == "2nd highest"


def test_a_change_gives_its_direction():
    claims, _ = _claims(series_case())
    (change, *_) = by_kind(claims, "change")
    assert change.word in ("rose", "rose sharply", "fell", "fell sharply", "stayed flat")
    assert change.word.startswith("rose")  # West: $2.11M in 2024 H1 to $2.63M in 2025 Jul–Nov


def test_a_target_gives_below_or_above():
    claims, facts = _claims(region_case())
    (south,) = [c for c in by_kind(claims, "extreme") if c.params["which"] == "max"]
    (west,) = [c for c in by_kind(claims, "extreme") if c.params["which"] == "min"]
    high, low = south.params["fact"], west.params["fact"]
    params = {"subject": "Fraud loss", "fact": low, "target": high, "better": "lower"}
    assert word_of("target", params, facts) == "below"
    assert word_of("target", {**params, "fact": high, "target": low}, facts) == "above"
    assert word_of("target", {**params, "target": low}, facts) == "at"


def test_a_value_claim_has_no_word():
    claims, _ = _claims(region_case())
    assert all(c.word is None for c in by_kind(claims, "value"))


def test_a_claim_names_its_member():
    claims, _ = _claims(region_case())
    (south,) = [c for c in by_kind(claims, "extreme") if c.params["which"] == "max"]
    assert "South" in south.about


# --- the checks of a word token -------------------------------------------------------------


@pytest.fixture
def regions():
    claims, facts = _claims(region_case())
    by_id = {c.id: c for c in claims}
    top = next(c for c in by_kind(claims, "extreme") if c.params["which"] == "max")
    low = next(c for c in by_kind(claims, "extreme") if c.params["which"] == "min")
    return by_id, facts, top, low


def test_a_sentence_with_the_value_and_the_member_passes(regions):
    by_id, facts, top, low = regions
    text = (f"South had the {{word:{top.id}}} fraud loss at {{fact:{top.params['fact']}}}, "
            f"and West the {{word:{low.id}}} at {{fact:{low.params['fact']}}}.")
    assert check_word_tokens(text, "p", by_id) == []
    assert check_text(text, "p", facts, by_id) == []
    assert check_words(text, "p") == []  # the words come from tokens, not from the model


def test_a_word_without_its_value_is_rejected(regions):
    by_id, _, top, _ = regions
    (issue,) = check_word_tokens(f"South had the {{word:{top.id}}} fraud loss.", "p", by_id)
    assert issue.code == "word_without_value"


def test_a_word_next_to_another_member_is_rejected(regions):
    """"West had the highest fraud loss at $15.84M": the value is South's, the name is West."""
    by_id, _, top, _ = regions
    text = f"West had the {{word:{top.id}}} fraud loss at {{fact:{top.params['fact']}}}."
    codes = {i.code for i in check_word_tokens(text, "p", by_id)}
    assert codes == {"word_wrong_member"}


def test_a_word_must_name_its_member(regions):
    by_id, _, top, _ = regions
    text = f"The {{word:{top.id}}} fraud loss was {{fact:{top.params['fact']}}}."
    (issue,) = check_word_tokens(text, "p", by_id)
    assert issue.code == "word_wrong_member" and "South" in issue.message


def test_a_negated_word_is_rejected(regions):
    by_id, _, top, _ = regions
    text = f"South did not have the {{word:{top.id}}} fraud loss ({{fact:{top.params['fact']}}})."
    (issue,) = check_word_tokens(text, "p", by_id)
    assert issue.code == "negated_word"


def test_a_claim_with_no_word_cannot_give_one(regions):
    by_id, _, _, _ = regions
    value = next(c for c in by_id.values() if c.kind == "value")
    (issue,) = check_word_tokens(f"It was {{word:{value.id}}}.", "p", by_id)
    assert issue.code == "no_word"


def test_an_unknown_word_token_is_rejected(regions):
    by_id, facts, _, _ = regions
    (issue,) = check_text("It was {word:q9.nothing}.", "p", facts, by_id)
    assert issue.code == "unknown_claim"


# --- rendering and counting -----------------------------------------------------------------


def test_render_writes_the_word(regions):
    by_id, _, top, _ = regions
    assert render_claims(f"South had the {{word:{top.id}}} loss.", by_id) == "South had the highest loss."


def test_a_word_token_cites_its_claim(regions):
    from living_reports.spec.models import Prose, Section
    _, _, top, _ = regions
    section = Section(id="s1", heading="Loss", intent="why", blocks=[Prose(text=f"South had the {{word:{top.id}}} loss.")])
    assert cited_claims(section) == [top.id]


def test_a_word_token_counts_as_one_word_and_one_claim():
    sentence = "South had the {word:a} loss at {fact:x}, and West the {word:b} at {fact:y}, and {claim:c}."
    (problem,) = ste_problems([sentence])
    assert problem.startswith("crowded_sentence:") and "3 claims" in problem
    assert style_problems(["South had the {word:a} loss at {fact:x}."]) == []


def test_the_judge_sees_a_word_token_as_a_cited_word():
    assert free_words(["South had the {word:a} loss at {fact:x}."]) == \
        "South had the [cited word] loss at [value]."


def test_a_word_token_is_not_allowed_in_a_title(regions):
    from living_reports.spec.validate import _plain
    by_id, facts, top, _ = regions
    codes = {i.code for i in _plain(f"The {{word:{top.id}}} region", "title", facts, by_id, True)}
    assert "token_in_title" in codes


# --- the writer -----------------------------------------------------------------------------

LOSS_BY_CHANNEL = {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]}


def routed(query):
    return {"objective": "why", "query": query, "clarify": None, "assumptions": [], "out_of_scope": None}


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def _section_claims(ctx):
    from living_reports.engine import Query
    from living_reports.harness.builder import run_section
    query = Query(**LOSS_BY_CHANNEL)
    results, facts = run_section(query, "q1", ctx)
    out = claims_for(query, "q1", results, facts, "Where?")
    return out.claims


def test_the_prompt_lists_each_claim_word_and_its_values(ctx, fake_llm):
    claims = _section_claims(ctx)
    top = next(c for c in by_kind(claims, "extreme") if c.params["which"] == "max")
    fake_llm.push(routed(LOSS_BY_CHANNEL), {"title": "Loss", "heading": "Loss", "action": None,
                                            "paragraphs": [f"{{claim:{top.id}}}."]})
    answer("Where?", None, ctx)
    prompt = fake_llm.prompts[1]
    assert f"{{word:{top.id}}}" in prompt and '"highest"' in prompt
    assert f"{{fact:{top.params['fact']}}}" in prompt


def test_a_draft_with_word_tokens_is_published_and_covers_its_claims(ctx, fake_llm):
    claims = _section_claims(ctx)
    top = next(c for c in by_kind(claims, "extreme") if c.params["which"] == "max")
    covered = claims[:2]
    sentences = []
    for c in covered:
        if c.word and c.about:
            fact = c.params.get("fact") or c.params.get("last")
            sentences.append(f"For {c.about[-1]}, it was {{word:{c.id}}} at {{fact:{fact}}}.")
        else:
            sentences.append(f"In short, {{claim:{c.id}}}.")
    draft = {"title": "Loss", "heading": "Where the loss is", "action": None, "paragraphs": [" ".join(sentences)]}
    fake_llm.push(routed(LOSS_BY_CHANNEL), draft)
    report = ctx.reports.get(answer("Where?", None, ctx).report_id)
    section = report.spec.sections[0]
    assert section.narration == "model"
    (prose, *_) = [b.text for b in section.blocks if b.type == "prose"]
    assert prose == draft["paragraphs"][0]  # no lead claim was added: the words cover the claims
    assert set(c.id for c in covered) <= set(cited_claims(section))
    assert top.id in report.spec.claims


def test_a_typed_finding_word_is_still_rejected(ctx, fake_llm):
    claims = _section_claims(ctx)
    top = next(c for c in by_kind(claims, "extreme") if c.params["which"] == "max")
    typed = {"title": "Loss", "heading": "Loss", "action": None,
             "paragraphs": [f"{top.params['member']} had the highest loss at {{fact:{top.params['fact']}}}."]}
    fake_llm.push(routed(LOSS_BY_CHANNEL), typed, typed, typed)
    answer("Where?", None, ctx)
    assert "claim_word" in fake_llm.prompts[2]
    assert "{word:" in fake_llm.prompts[2]  # the repair note names the word token to use


def test_claim_model_defaults_keep_old_reports_valid():
    old = Claim.model_validate({"id": "q1.value.x", "kind": "value", "text": "t", "facts": [], "query_ref": "q1"})
    assert old.word is None and old.about == []


def test_a_trend_gives_its_steps_as_one_word():
    """West by full half-year: $2.1M, $2.3M, $2.2M (the partial Jul–Nov 2025 is left out)."""
    claims, _ = _claims(series_case())
    (trend,) = by_kind(claims, "trend")
    assert trend.word == "went up and down"
    assert trend.text.startswith("Fraud loss for West went up and down:")


def test_a_trend_word_needs_one_of_its_points(regions):
    claims, facts = _claims(series_case())
    (trend,) = by_kind(claims, "trend")
    by_id = {c.id: c for c in claims}
    first, last = trend.params["points"][0], trend.params["points"][-1]
    ok = f"Fraud loss for West {{word:{trend.id}}}, from {{fact:{first}}} to {{fact:{last}}}."
    assert check_word_tokens(ok, "p", by_id) == []
    (issue,) = check_word_tokens(f"Fraud loss for West {{word:{trend.id}}}.", "p", by_id)
    assert issue.code == "word_without_value"


def test_r1_asks_about_the_writers_own_additions():
    from living_reports.harness.judge import RUBRIC, only_connectors
    question, _, note = RUBRIC["r1"]
    assert "[cited word]" in question and "add" in question
    assert "outside the cited claims" in note
    assert only_connectors("In short, [cited claim]. Also [cited word] [value].")
