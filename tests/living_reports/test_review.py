"""How a section's own words were checked, apart from its claims.

Code proves each cited claim against the data. The words between the claims get a weaker
check: the word guard (a list), then a decision model that answers with a probability. A
section stores which of these ran, so that the report can show the two levels apart.
"""
import pytest

from living_reports.harness.builder import AskContext, answer
from living_reports.harness.claims import outdated_reviews
from living_reports.metrics.runner import Fact
from living_reports.reports.store import ReportStore
from living_reports.spec.models import Review

LOSS_BY_CHANNEL = {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]}
WORDS = {"title": "Loss", "heading": "Where the loss is", "action": None}
CONNECTORS = {**WORDS, "paragraphs": ["In short, {claim:q1.value.fraud_loss}."]}
STORY = {**WORDS, "paragraphs": ["The channels tell a clear story here: {claim:q1.value.fraud_loss}."]}


def routed(query):
    return {"objective": "why", "query": query, "clarify": None, "assumptions": [], "out_of_scope": None}


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


@pytest.fixture
def judge(monkeypatch):
    """A decision model that passes every rubric item."""
    from living_reports.harness import decide
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    passing = {"r1": 0.05, "r2": 0.95, "r3": 0.05, "r4": 0.95}

    def post(url, body, timeout):
        if set(body["questions"]) & {"answerable", "next"}:
            return {"answers": {}}
        return {"answers": {n: {"type": "noul", "noul": passing[n]} for n in body["questions"]}}

    monkeypatch.setattr(decide, "_post", post)


def _review(ctx, fake_llm, words):
    fake_llm.push(routed(LOSS_BY_CHANNEL), words)
    return ctx.reports.get(answer("Where is the loss?", None, ctx).report_id).spec.sections[0].review


def test_free_words_that_a_model_reviewed_are_marked_reviewed_not_proven(ctx, fake_llm, judge):
    assert _review(ctx, fake_llm, STORY) == Review(wording="model", omissions="model", model="ollama/d3:4b")


def test_only_connecting_words_are_checked_by_code(ctx, fake_llm, judge):
    review = _review(ctx, fake_llm, CONNECTORS)
    assert review.wording == "connectors" and review.omissions == "model"


def test_with_no_decision_model_the_words_are_not_reviewed(ctx, fake_llm, monkeypatch):
    monkeypatch.delenv("LR_DECIDE_MODEL", raising=False)
    monkeypatch.delenv("LR_JUDGE_MODEL", raising=False)
    assert _review(ctx, fake_llm, STORY) == Review(wording="none", omissions="none")


def test_with_no_decision_model_connecting_words_are_still_checked_by_code(ctx, fake_llm, monkeypatch):
    monkeypatch.delenv("LR_DECIDE_MODEL", raising=False)
    monkeypatch.delenv("LR_JUDGE_MODEL", raising=False)
    assert _review(ctx, fake_llm, CONNECTORS) == Review(wording="connectors", omissions="none")


def test_a_section_that_code_wrote_has_fixed_wording(ctx, fake_llm):
    bad = {**WORDS, "paragraphs": ["Loss rose 38%."]}
    fake_llm.push(routed(LOSS_BY_CHANNEL), bad, bad, bad)
    section = ctx.reports.get(answer("Where?", None, ctx).report_id).spec.sections[0]
    assert section.narration == "code"
    assert section.review == Review(wording="code", omissions="none")


def test_a_rewrite_reviews_the_new_words(ctx, fake_llm, judge):
    from living_reports.harness.commands import rewrite_section
    fake_llm.push(routed(LOSS_BY_CHANNEL), CONNECTORS)
    report = ctx.reports.get(answer("Where?", None, ctx).report_id)
    fake_llm.push({"paragraphs": ["Here is the clear picture: {claim:q1.value.fraud_loss}."]})
    saved = rewrite_section(report, "s1", "shorter", ctx)
    assert saved.spec.sections[0].review.wording == "model"


def _fact(fid, value):
    return Fact(id=fid, label=fid, value=value, formatted=str(value), metric="fraud_loss",
                metric_version=1, query_hash="h", snapshot="s1")


def test_a_review_is_outdated_when_a_value_it_read_changes(ctx, fake_llm, judge):
    fake_llm.push(routed(LOSS_BY_CHANNEL), STORY)
    report = ctx.reports.get(answer("Where?", None, ctx).report_id)
    cited = report.spec.claims["q1.value.fraud_loss"].facts[0]
    moved = {**report.facts, cited: _fact(cited, report.facts[cited].value + 1)}
    assert outdated_reviews(report.spec, report.facts, moved).sections[0].review.outdated
    same = outdated_reviews(report.spec, report.facts, dict(report.facts))
    assert not same.sections[0].review.outdated


def test_a_check_by_code_does_not_go_out_of_date():
    """Connecting words state no fact, so new values cannot make them false."""
    from living_reports.spec.models import Prose, ReportSpec, Section
    section = Section(id="s1", heading="h", intent="q", blocks=[Prose(text="Also {fact:a}.")],
                      review=Review(wording="connectors", omissions="none"))
    spec = ReportSpec(title="t", kicker="k", objective="why", assumptions=[], sections=[section])
    out = outdated_reviews(spec, {"a": _fact("a", 1.0)}, {"a": _fact("a", 2.0)})
    assert not out.sections[0].review.outdated


def test_a_refresh_with_new_values_marks_the_review_outdated(orders_file, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    from living_reports.api.app import create_app
    from living_reports.store import get_store
    with TestClient(create_app()) as client:
        fake_llm.push(routed(LOSS_BY_CHANNEL), STORY)
        rid = client.post("/api/ask", json={"question": "Where?"}, headers={"X-LR-User": "ana"}).json()["report_id"]
        reports = ReportStore(get_store())
        report = reports.get(rid)
        report.spec.sections[0].review = Review(wording="model", omissions="model", model="ollama/d3:4b")
        reports.save(report)
        fake_llm.push({"claims": []})
        client.post("/api/sources/duckdb_demo/advance")
        review = client.get(f"/api/reports/{rid}").json()["spec"]["sections"][0]["review"]
    assert review == {"wording": "model", "omissions": "model", "model": "ollama/d3:4b", "outdated": True}
