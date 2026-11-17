"""Plain-English rules for the writer's own words.

Code checks the words that the writer adds between claim tokens: a sentence of at most 25
words (a token counts as one word), at most 2 claims in a sentence, and plain words in place
of a short list of hard ones. Each failed rule goes back to the writer as a repair note.
"""
import pytest

from living_reports.harness.builder import AskContext, answer, ste_problems
from living_reports.reports.store import ReportStore

LOSS_BY_CHANNEL = {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]}
WORDS = {"title": "Loss", "heading": "Where the loss is", "action": None}
CITED = {**WORDS, "paragraphs": ["In short, {claim:q1.value.fraud_loss}."]}


def routed(query):
    return {"objective": "why", "query": query, "clarify": None, "assumptions": [], "out_of_scope": None}


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def test_a_short_sentence_with_tokens_passes():
    assert ste_problems(["In short, {claim:q1.a} and {claim:q1.b}."]) == []


def test_a_sentence_over_25_words_is_sent_back():
    long = " ".join(["word"] * 25) + " {claim:q1.a}."  # 26 words: the token counts as one
    (problem,) = ste_problems([long])
    assert problem.startswith("long_sentence:") and "26 words" in problem


def test_a_token_counts_as_one_word():
    assert ste_problems([" ".join(["word"] * 24) + " {claim:q1.a}."]) == []


def test_three_claims_in_one_sentence_are_sent_back():
    (problem,) = ste_problems(["Also {claim:q1.a}, {claim:q1.b}, and {claim:q1.c}."])
    assert problem.startswith("crowded_sentence:") and "3 claims" in problem


@pytest.mark.parametrize("hard, plain", [
    ("utilize", "use"), ("prior to", "before"), ("in order to", "to"), ("approximately", "about")])
def test_a_hard_word_gets_its_plain_word(hard, plain):
    (problem,) = ste_problems([f"We {hard} this: {{claim:q1.a}}."])
    assert problem.startswith("plain_words:") and f'"{plain}"' in problem and f'"{hard}"' in problem


def test_a_hard_word_inside_a_claim_token_is_not_checked():
    assert ste_problems(["{claim:q1.prior_to_utilize}."]) == []


def test_a_long_draft_goes_back_to_the_writer(ctx, fake_llm):
    long = {**WORDS, "paragraphs": [" ".join(["word"] * 26) + " {claim:q1.value.fraud_loss}."]}
    fake_llm.push(routed(LOSS_BY_CHANNEL), long, CITED)
    report = ctx.reports.get(answer("Where?", None, ctx).report_id)
    assert "long_sentence" in fake_llm.prompts[2]
    assert report.spec.sections[0].narration == "model"


@pytest.fixture
def judge(monkeypatch):
    from living_reports.harness import decide
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    states = []
    passing = {"r1": 0.05, "r2": 0.95, "r3": 0.05, "r4": 0.95}

    def post(url, body, timeout):
        if set(body["questions"]) & {"answerable", "next"}:
            return {"answers": {}}
        states.append((body["state"], set(body["questions"])))
        return {"answers": {n: {"type": "noul", "noul": passing[n]} for n in body["questions"]}}

    monkeypatch.setattr(decide, "_post", post)
    return states


def test_the_judge_does_not_read_the_title_or_the_heading(ctx, fake_llm, judge):
    """A 4B judge read plain labels such as "Trend over time." as stated facts, and sent good
    sections back. The word guard checks the title; the judge reads only the paragraphs."""
    titled = {**CITED, "title": "Telesales leads fraud loss", "heading": "Telesales is ahead"}
    fake_llm.push(routed(LOSS_BY_CHANNEL), titled)
    answer("Where?", None, ctx)
    ((state, questions),) = judge
    assert "Telesales" not in state["free_text"]
    assert "r1" not in questions  # the paragraphs are only connecting words


def test_connecting_words_alone_still_mark_the_wording_as_checked_by_code(ctx, fake_llm, judge):
    """The title goes to the judge, but the record of the words between the claims reads only
    the paragraphs."""
    fake_llm.push(routed(LOSS_BY_CHANNEL), CITED)
    report = ctx.reports.get(answer("Where?", None, ctx).report_id)
    assert report.spec.sections[0].review.wording == "connectors"
