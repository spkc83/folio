import pytest

from living_reports.store import get_store
from living_reports.semantic.demo import seed_demo
from living_reports.harness.builder import AskContext
from living_reports.harness.ladder import run_ladder
from living_reports.reports.store import ReportStore
from living_reports.sandbox.runner import deno_path

ROUTED = {"objective": "why", "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Orders", "heading": "Volume", "paragraphs": ["Orders: {fact:q1_total.order_count}."], "action": None}
CODE = """
const a = await tools.metrics_query({metrics: ['fraud_loss'], dimensions: ['orders.sales_channel'], limit: 1});
const top = a.rows[0][0];
const b = await tools.metrics_query({metrics: ['order_count'], dimensions: ['dealers.region'], filters: [{member: 'orders.sales_channel', op: 'eq', values: [top]}]});
await tools.report_add_section({heading: 'Top channel', paragraphs: ['The top channel has {fact:q2_total.order_count} orders.']});
"""


@pytest.fixture
def ctx(store2, monkeypatch):
    monkeypatch.setenv("LR_SMALL_MODEL", "ollama/small")
    monkeypatch.setenv("LR_FRONTIER_MODEL", "ollama/frontier")
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def fresh(ctx):
    return AskContext(store=ctx.store, reports=ctx.reports, source_id=ctx.source_id)


def test_simple_question_stays_on_the_small_model(ctx, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    r = run_ladder("Orders by region?", None, ctx)
    assert r.rung == "small_spec" and r.answer.kind == "report" and r.answer.cost.rung == "small_spec"


def test_repeated_question_uses_cache_with_no_tokens(ctx, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    first = run_ladder("Orders by region?", None, ctx)
    second = run_ladder("  orders by REGION? ", None, fresh(ctx))
    assert second.rung == "cache" and second.answer.report_id == first.answer.report_id
    assert second.answer.cost.calls == 0 and second.answer.cost.usd == 0.0


def test_answer_cache_key_changes_with_data_time(ctx, fake_llm):
    from living_reports.harness.ladder import _key
    from living_reports.sources.build import build_demo_source
    fake_llm.push(ROUTED, WORDS, ROUTED, WORDS)
    before = _key("Orders by region?", ctx)
    run_ladder("Orders by region?", None, ctx)
    build_demo_source(1)
    assert _key("Orders by region?", ctx) != before
    assert run_ladder("Orders by region?", None, fresh(ctx)).rung == "small_spec"


def test_cache_does_not_survive_a_definition_change(ctx, fake_llm):
    fake_llm.push(ROUTED, WORDS, ROUTED, WORDS)
    run_ladder("Orders by region?", None, ctx)
    node = ctx.store.graph.slice().node("order_count")
    region = {"member": "orders.sales_channel", "op": "ne", "values": ["x"]}
    payload = {**node.payload, "measure": {**node.payload["measure"], "filter": region}}
    ctx.store.graph.put("order_count", "MetricBinding", payload, valid_from=node.valid_from.replace(year=2025))
    assert run_ladder("Orders by region?", None, fresh(ctx)).rung == "small_spec"


def test_validation_failure_moves_up_and_costs_are_kept(ctx, fake_llm):
    fake_llm.push("x", "y", "z", ROUTED, WORDS)
    r = run_ladder("Orders by region?", None, ctx)
    assert r.rung == "frontier" and r.answer.kind == "report"
    assert r.attempts == ["small_spec", "frontier"] and r.answer.cost.calls == 5


def test_bad_narration_moves_up(ctx, fake_llm):
    """The small rung's failed drafts escalate; nothing is saved for the small rung."""
    bad = {**WORDS, "paragraphs": ["It is 5."]}
    fake_llm.push(ROUTED, bad, bad, bad, ROUTED, WORDS)
    r = run_ladder("Orders by region?", None, ctx)
    assert r.rung == "frontier" and r.attempts == ["small_spec", "frontier"]
    assert [x.id for x in ctx.reports.list_reports()] == [r.answer.report_id]
    assert ctx.reports.get(r.answer.report_id).spec.sections[0].narration == "model"


def test_the_last_rung_ends_in_code_text(ctx, fake_llm):
    bad = {**WORDS, "paragraphs": ["It is 5."]}
    fake_llm.push(ROUTED, bad, bad, bad, ROUTED, bad, bad, bad)
    r = run_ladder("Orders by region?", None, ctx)
    assert r.rung == "frontier" and r.answer.kind == "report"
    assert ctx.reports.get(r.answer.report_id).spec.sections[0].narration == "code"


def test_without_a_frontier_model_the_small_rung_ends_in_code_text(ctx, fake_llm, monkeypatch):
    monkeypatch.delenv("LR_FRONTIER_MODEL")
    bad = {**WORDS, "paragraphs": ["It is 5."]}
    fake_llm.push(ROUTED, bad, bad, bad)
    r = run_ladder("Orders by region?", None, ctx)
    assert r.rung == "small_spec" and r.attempts == ["small_spec"] and r.answer.kind == "report"
    assert ctx.reports.get(r.answer.report_id).spec.sections[0].narration == "code"


def test_all_rungs_failing_gives_a_message(ctx, fake_llm):
    fake_llm.push(*["x"] * 6)
    r = run_ladder("Orders by region?", None, ctx)
    assert r.answer.kind == "failed" and r.answer.message and r.attempts == ["small_spec", "frontier"]


def test_no_frontier_model_means_no_escalation(ctx, fake_llm, monkeypatch):
    monkeypatch.delenv("LR_FRONTIER_MODEL")
    fake_llm.push("x", "y", "z")
    r = run_ladder("Orders by region?", None, ctx)
    assert r.answer.kind == "failed" and r.attempts == ["small_spec"]


def test_out_of_scope_does_not_escalate(ctx, fake_llm):
    fake_llm.push({**ROUTED, "query": None, "out_of_scope": "No."})
    r = run_ladder("Staff?", None, ctx)
    assert r.answer.kind == "out_of_scope" and r.attempts == ["small_spec"]


def test_clarify_does_not_escalate_and_is_not_cached(ctx, fake_llm):
    clarify = {**ROUTED, "query": None, "clarify": {"question": "Which?", "options": [
        {"label": "Orders", "query": ROUTED["query"]}, {"label": "Loss", "query": {"metrics": ["fraud_loss"], "dimensions": ["dealers.region"]}}]}}
    fake_llm.push(clarify, clarify)
    assert run_ladder("How big?", None, ctx).answer.kind == "clarify"
    assert run_ladder("How big?", None, fresh(ctx)).answer.kind == "clarify"


def test_follow_up_skips_the_cache(ctx, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    rid = run_ladder("Orders by region?", None, ctx).answer.report_id
    fake_llm.push(ROUTED, {**WORDS, "paragraphs": ["Orders: {fact:q2_total.order_count}."]})
    r = run_ladder("Orders by region?", rid, fresh(ctx))
    assert r.rung == "small_spec" and len(ctx.reports.get(rid).spec.sections) == 2


@pytest.mark.skipif(deno_path() is None, reason="deno is not installed")
def test_multi_step_question_uses_code_mode(ctx, fake_llm):
    fake_llm.push({**ROUTED, "multi_step": True}, {"code": CODE})
    r = run_ladder("How many orders came from the channel with the most fraud loss?", None, ctx)
    assert r.rung == "code_mode" and r.answer.kind == "report"
    report = ctx.reports.get(r.answer.report_id)
    assert len(report.query_defs) == 2 and "q2_total.order_count" in report.facts
    assert report.spec.title and report.tier == "team"


@pytest.mark.skipif(deno_path() is None, reason="deno is not installed")
def test_code_mode_prompt_uses_declarations(ctx, fake_llm):
    fake_llm.push({**ROUTED, "multi_step": True}, {"code": CODE})
    run_ladder("Multi?", None, ctx)
    assert "declare const tools" in fake_llm.prompts[1] and "metrics_query" in fake_llm.prompts[1]


@pytest.mark.skipif(deno_path() is None, reason="deno is not installed")
def test_broken_code_moves_up(ctx, fake_llm):
    fake_llm.push({**ROUTED, "multi_step": True}, {"code": "throw new Error('x')"}, ROUTED, WORDS)
    r = run_ladder("Multi?", None, ctx)
    assert r.attempts == ["code_mode", "frontier"] and r.rung == "frontier"


@pytest.mark.skipif(deno_path() is None, reason="deno is not installed")
def test_code_that_invents_a_number_moves_up(ctx, fake_llm):
    bad = "await tools.metrics_query({metrics: ['order_count']}); await tools.report_add_section({heading: 'H', paragraphs: ['It is 99.']});"
    fake_llm.push({**ROUTED, "multi_step": True}, {"code": bad}, ROUTED, WORDS)
    assert run_ladder("Multi?", None, ctx).rung == "frontier"


@pytest.mark.skipif(deno_path() is None, reason="deno is not installed")
def test_code_mode_report_refreshes_on_new_data(ctx, fake_llm):
    from living_reports.reports.changes import refresh_report
    fake_llm.push({**ROUTED, "multi_step": True}, {"code": CODE})
    r = run_ladder("Multi?", None, ctx)
    report = ctx.reports.get(r.answer.report_id)
    assert refresh_report(report, ctx) is True


def test_api_reports_the_rung(orders_file, fake_llm):
    from fastapi.testclient import TestClient

    from living_reports.api.app import create_app
    with TestClient(create_app()) as c:
        fake_llm.push(ROUTED, WORDS)
        r = c.post("/api/ask", json={"question": "Orders by region?"}).json()
        assert r["cost"]["rung"] == "small_spec"
        assert c.post("/api/ask", json={"question": "Orders by region?"}).json()["cost"]["rung"] == "cache"


@pytest.fixture
def doubtful(monkeypatch):
    """A decision model that finds every question not answerable."""
    from living_reports.harness import decide
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    monkeypatch.setattr(decide, "_post", lambda url, body, timeout: {
        "answers": {"answerable": {"type": "noul", "noul": 0.02}}})


def test_a_doubt_from_the_decision_model_asks_the_next_rung(ctx, fake_llm, doubtful):
    fake_llm.push(ROUTED, ROUTED, WORDS)
    r = run_ladder("Orders by region?", None, ctx)
    assert r.attempts == ["small_spec", "frontier"] and r.answer.kind == "report"


def test_without_a_next_rung_the_doubt_is_a_refusal(ctx, fake_llm, doubtful, monkeypatch):
    monkeypatch.delenv("LR_FRONTIER_MODEL")
    fake_llm.push(ROUTED)
    r = run_ladder("Orders by region?", None, ctx)
    assert r.answer.kind == "out_of_scope" and r.answer.can_explore and r.attempts == ["small_spec"]


def test_the_cache_key_changes_with_the_decision_settings(ctx, monkeypatch):
    from living_reports.harness.ladder import _key
    before = _key("Orders by region?", ctx)
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    with_decider = _key("Orders by region?", ctx)
    monkeypatch.setenv("LR_JUDGE_FLOOR", "0.7")
    assert len({before, with_decider, _key("Orders by region?", ctx)}) == 3


def test_the_cache_key_has_the_narration_version(ctx, monkeypatch):
    from living_reports.harness import ladder
    assert ladder.NARRATION_VERSION in ladder._pipeline()
    before = ladder._key("Orders by region?", ctx)
    monkeypatch.setattr(ladder, "NARRATION_VERSION", "a-later-version")
    assert ladder._key("Orders by region?", ctx) != before
