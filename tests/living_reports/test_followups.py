"""Follow-up chips: code makes every candidate, a decision model ranks them, the person clicks.

The insight eval found that topic questions ("How is attrition moving in Engineering?") get a
route without the breakdown that holds the story. The chip that would find it came from a
fixed rule: the first unused dimension in vocabulary order.
"""
import pytest

from living_reports.engine import Query
from living_reports.harness.builder import AskContext, answer
from living_reports.harness.followups import MAX_FOLLOW_UPS, candidates, rank
from living_reports.reports.store import ReportStore
from living_reports.spec.models import FollowUp

CHANNEL = "orders.sales_channel"
REGION = "dealers.region"


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def test_each_unused_dimension_is_a_candidate_and_time_comes_once(ctx):
    model = ctx.store.graph.model()
    found = candidates(Query(metrics=["fraud_loss"], dimensions=[CHANNEL]), model, top=("App", CHANNEL))
    labels = [c.label for c in found]
    assert "By Region" in labels and "Over time" in labels
    assert "App by Region" in labels
    assert not any(c.query.get("dimensions") == [CHANNEL] for c in found)  # not the section itself
    assert len(labels) == len(set(labels))


def test_a_candidate_is_a_query_that_compiles(ctx):
    model = ctx.store.graph.model()
    for c in candidates(Query(metrics=["fraud_loss"]), model):
        Query.model_validate(c.query)  # each one is a governed query, ready to run


def test_a_section_over_time_gets_no_over_time_candidate(ctx):
    model = ctx.store.graph.model()
    found = candidates(Query(metrics=["fraud_loss"], time={"grain": "month"}), model)
    assert "Over time" not in [c.label for c in found]


def _decider(monkeypatch, probabilities):
    from living_reports.harness import decide
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    monkeypatch.setenv("LR_RANK_FOLLOW_UPS", "true")
    sent = []

    def post(url, body, timeout):
        sent.append(body)
        question = body["questions"]["next"]
        probs = {k: probabilities.get(question["criteria"][k], 0.0) for k in question["criteria"]}
        return {"answers": {"next": {"type": "choice", "choice": max(probs, key=probs.get),
                                     "probabilities": probs}}}

    monkeypatch.setattr(decide, "_post", post)
    return sent


def _three():
    return [FollowUp(label=x, query={"metrics": ["fraud_loss"]}) for x in ("By A", "By B", "By C")]


def test_the_decision_model_orders_the_candidates(monkeypatch):
    sent = _decider(monkeypatch, {"By C": 0.7, "By A": 0.2, "By B": 0.1})
    ranked = rank(_three(), "How is fraud loss doing?", "Fraud loss was $1.0M", "t")
    assert [c.label for c in ranked] == ["By C", "By A", "By B"]
    assert sent[0]["state"]["finding"] == "Fraud loss was $1.0M"


def test_with_no_decision_model_the_order_stays(monkeypatch):
    monkeypatch.delenv("LR_DECIDE_MODEL", raising=False)
    many = [FollowUp(label=f"By {i}", query={"metrics": ["fraud_loss"]}) for i in range(6)]
    ranked = rank(many, "q", "finding", "t")
    assert [c.label for c in ranked] == [f"By {i}" for i in range(MAX_FOLLOW_UPS)]


def test_a_new_section_stores_its_ranked_follow_ups(ctx, fake_llm):
    fake_llm.push({"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL]},
                   "clarify": None, "assumptions": [], "out_of_scope": None},
                  {"title": "Loss", "heading": "Loss", "paragraphs": ["{claim:q1.value.fraud_loss}."],
                   "action": None})
    report = ctx.reports.get(answer("Where is the loss?", None, ctx).report_id)
    follow = report.spec.sections[0].follow_ups
    assert 1 <= len(follow) <= MAX_FOLLOW_UPS
    assert follow[0].label == "By Region"  # no decision model in the tests: vocabulary order


def test_the_ranking_is_off_by_default(monkeypatch):
    sent = _decider(monkeypatch, {"By C": 0.7, "By A": 0.2, "By B": 0.1})
    monkeypatch.delenv("LR_RANK_FOLLOW_UPS")
    assert [c.label for c in rank(_three(), "q", "finding", "t")] == ["By A", "By B", "By C"]
    assert sent == []  # no decision call


def test_the_chip_ranker_can_be_its_own_model(monkeypatch):
    """LR_RANK_MODEL: Gemini Decision 1 ranks the chips while the local model routes."""
    from living_reports.harness import decide
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    monkeypatch.setenv("LR_RANK_MODEL", "gemini/gemini-decision-1")
    monkeypatch.setenv("LR_RANK_FOLLOW_UPS", "true")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    sent = []

    def post(url, body, timeout, headers=None):
        sent.append((url, body["model"]))
        return {"answers": {"next": {"type": "choice", "choice": "c",
                                     "probabilities": {"a": 0.1, "b": 0.2, "c": 0.7}}}}

    monkeypatch.setattr(decide, "_post", post)
    assert [c.label for c in rank(_three(), "q", "finding", "t")] == ["By C", "By B", "By A"]
    assert sent == [(decide.GEMINI_URL, "gemini-decision-1")]
    assert decide.model_for("decide") == "ollama/d3:4b"


APP = {"member": CHANNEL, "op": "eq", "values": ["App"]}


def _chip_claims(ctx, question, dimensions=(REGION,)):
    from living_reports.harness.builder import run_section
    from living_reports.harness.claims import claims_for
    query = Query(metrics=["fraud_loss", "fraud_rate"], dimensions=list(dimensions), filters=[APP])
    results, facts = run_section(query, "q2", ctx)
    out = claims_for(query, "q2", results, facts, question)
    return out.claims, out.facts({f.id: f for f in facts})


def test_a_chip_section_leads_with_the_largest_move_of_its_member(ctx):
    """The insight eval: the chip "Electronics by Brand" found "Electronics gross margin fell
    sharply", but the text stated the top brand, the target, and the total. A section narrowed
    to the member that the chip names leads with that member's largest change."""
    from living_reports.harness.claims import _worse
    claims, facts = _chip_claims(ctx, "App by Region")
    moves = [c for c in claims if c.kind == "change" and c.segment == {CHANNEL: "App"}]
    assert len(moves) >= 2

    def key(c):
        d = facts[c.params["pct"]].value
        return _worse(c.params.get("better"), d), abs(d)
    assert claims[0].id == max(moves, key=key).id
    assert claims[1].id == "q2.extreme.max.fraud_loss"  # the leader before stays second


def test_a_section_that_does_not_name_its_member_keeps_its_order(ctx):
    claims, _ = _chip_claims(ctx, "Where is the loss by region?")
    assert claims[0].id == "q2.extreme.max.fraud_loss"


def test_a_named_member_with_no_breakdown_keeps_its_order(ctx):
    """"Does App meet the fraud rate target?" asks about the target, not the move."""
    claims, _ = _chip_claims(ctx, "Does App meet the fraud rate target?", dimensions=())
    assert claims[0].kind != "change"


@pytest.mark.parametrize("better, change, worse", [
    ("higher", -0.27, True), ("higher", 0.4, False), ("lower", 0.1, True), (None, -0.5, False)])
def test_a_move_against_the_better_direction_is_worse(better, change, worse):
    """A falling margin (worse) leads a rising revenue (better), whatever their sizes."""
    from living_reports.harness.claims import _worse
    assert _worse(better, change) is worse
