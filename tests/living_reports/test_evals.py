from pathlib import Path

import pytest
import yaml

from living_reports.store import get_store
from living_reports.evals.run import EvalQuestion, evaluate, load_questions, score_answer
from living_reports.semantic.demo import seed_demo
from living_reports.harness.builder import AskContext
from living_reports.harness.llm import TurnCost
from living_reports.engine import OutOfScope, Query
from living_reports.engine.compile import compile
from living_reports.metrics.runner import run_query
from living_reports.reports.store import ReportStore

ROUTED = {"objective": "status", "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Orders", "heading": "Volume", "paragraphs": ["Orders: {fact:q1_total.order_count}."], "action": None}


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def q(**kw):
    base = {"question": "Orders by region?", "expected_kind": "report",
            "expected_query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]}}
    return EvalQuestion(**{**base, **kw})


def test_question_file_is_large_and_valid(ctx):
    questions = load_questions()
    assert len(questions) >= 30
    assert {x.expected_kind for x in questions} == {"report", "clarify", "out_of_scope"}
    for item in questions:
        if item.expected_query:
            compile(Query.model_validate(item.expected_query), ctx.store.graph.model())


def test_right_query_is_correct(ctx, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    assert evaluate([q()], "small", 1, ctx)["correct"] == 1


def test_wrong_value_is_incorrect(ctx, fake_llm):
    fake_llm.push({**ROUTED, "query": {"metrics": ["fraud_loss"], "dimensions": ["dealers.region"]}}, {**WORDS, "paragraphs": ["ok"]})
    assert evaluate([q()], "small", 1, ctx)["correct"] == 0


def test_a_different_query_with_the_same_result_is_correct(ctx):
    expected = Query(metrics=["order_count"], filters=[{"member": "dealers.region", "op": "in", "values": ["East", "West"]}])
    actual = Query(metrics=["order_count"])
    assert score_answer(actual, expected, "duckdb_demo", ctx.store)


def test_row_order_does_not_change_the_score(ctx):
    a = Query(metrics=["order_count"], dimensions=["dealers.region"])
    b = Query(metrics=["order_count"], dimensions=["dealers.region"], limit=1000)
    assert score_answer(a, b, "duckdb_demo", ctx.store)


def test_out_of_scope_expected(ctx, fake_llm):
    fake_llm.push({**ROUTED, "query": None, "out_of_scope": "No."})
    item = q(expected_kind="out_of_scope", expected_query=None)
    assert evaluate([item], "small", 1, ctx)["correct"] == 1


def test_a_report_for_an_unanswerable_question_is_incorrect(ctx, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    item = q(expected_kind="out_of_scope", expected_query=None)
    assert evaluate([item], "small", 1, ctx)["correct"] == 0


def test_usd_per_correct(ctx, fake_llm, monkeypatch):
    monkeypatch.setenv("LR_SMALL_MODEL", "ollama/x")
    fake_llm.push(ROUTED, WORDS)
    result = evaluate([q()], "small", 1, ctx)
    assert result["usd_per_correct"] == 0.0 and result["accuracy"] == 1.0


def test_unknown_price_gives_null(ctx, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    assert evaluate([q()], "small", 1, ctx)["usd_per_correct"] is None


def test_no_correct_answers_gives_null(ctx, fake_llm, monkeypatch):
    monkeypatch.setenv("LR_SMALL_MODEL", "ollama/x")
    fake_llm.push({**ROUTED, "query": None, "out_of_scope": "No."})
    assert evaluate([q()], "small", 1, ctx)["usd_per_correct"] is None


def test_a_failed_turn_is_incorrect_and_the_run_continues(ctx, fake_llm):
    fake_llm.push("x", "y", "z", ROUTED, WORDS)
    assert evaluate([q(), q()], "small", 1, ctx)["correct"] == 1


def test_questions_file_is_yaml_list():
    data = yaml.safe_load(Path("living_reports/evals/questions.yaml").read_text())
    assert isinstance(data, list)


def test_out_of_scope_entries_really_are(ctx):
    for item in load_questions():
        if item.expected_kind == "out_of_scope":
            assert item.expected_query is None
    with pytest.raises(OutOfScope):
        compile(Query(metrics=["profit"]), ctx.store.graph.model())


def test_cost_type_is_summable():
    assert TurnCost().calls == 0


def test_every_expected_query_runs(ctx):
    for item in load_questions():
        if item.expected_query:
            result = run_query(Query.model_validate(item.expected_query), ctx.source_id, ctx.store)
            # The fixture data has no Partner channel, so that question has no rows here.
            assert result.row_count >= 1 or "Partner" in item.question, item.question


def test_main_runs_on_the_demo_source_at_moment_2(orders_file, fake_llm, monkeypatch, tmp_path):
    import json
    import sys
    from living_reports.evals import run as run_module
    fake_llm.push(ROUTED, WORDS)
    out = tmp_path / "out.json"
    seen = []
    real = run_module.evaluate

    def spy(questions, tier, runs, ctx, ask):
        seen.append(ctx.source_id)
        first = Query.model_validate({"metrics": ["order_count"]})
        seen.append(run_query(first, ctx.source_id, ctx.store).rows)
        return real(questions[:1], tier, runs, ctx, ask=ask)

    monkeypatch.setattr(run_module, "evaluate", spy)
    monkeypatch.setattr(sys, "argv", ["run", "--tier", "small", "--runs", "1", "--limit", "1",
                                      "--out", str(out)])
    run_module.main()
    assert seen == ["duckdb_demo", [[3000]]]
    assert json.loads(out.read_text())["total"] == 1


# --- narration (hypothesis H2: fallbacks and drafts per section across model sizes) ------


def test_a_draft_that_passes_counts_as_model_written(ctx, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    n = evaluate([q()], "small", 1, ctx)["narration"]
    assert (n["sections"], n["model_written"], n["code_written"]) == (1, 1, 0)
    assert (n["drafts"], n["first_draft_passed"], n["outcomes"]) == (1, 1, {"passed": 1})
    assert n["fallback_rate"] == 0.0 and n["drafts_per_section"] == 1.0


def test_three_rejected_drafts_end_in_a_code_written_section(ctx, fake_llm):
    bad = {**WORDS, "paragraphs": ["There were 5 orders."]}  # a digit outside a token
    fake_llm.push(ROUTED, bad, bad, bad)
    result = evaluate([q()], "small", 1, ctx)
    n = result["narration"]
    assert (n["model_written"], n["code_written"], n["fallback_rate"]) == (0, 1, 1.0)
    assert (n["drafts"], n["first_draft_passed"], n["outcomes"]) == (3, 0, {"code_check": 3})
    assert result["answers"][0]["narration"] == ["code"]
    assert result["answers"][0]["drafts"] == ["code_check"] * 3
    # Each rejected draft keeps its problems, so a run shows why drafts fail.
    notes = result["answers"][0]["notes"]
    assert len(notes) == 3 and all("raw_number" in " ".join(n) for n in notes)
    # And its text, so a person can check each rejection by hand.
    texts = result["answers"][0]["texts"]
    assert len(texts) == 3 and all(t.endswith("]. There were 5 orders.") for t in texts)


def test_each_answer_records_who_routed_it(ctx, fake_llm):
    # No decision model: the language model routes, and the summary counts it.
    fake_llm.push(ROUTED, WORDS)
    result = evaluate([q()], "small", 1, ctx)
    assert result["answers"][0]["routes"] == ["model"] and result["answers"][0]["correct"] is True
    assert result["routing"] == {"decision": 0, "model": 1, "decision_correct": 0, "model_correct": 1}
