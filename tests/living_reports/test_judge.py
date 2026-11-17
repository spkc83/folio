"""The narrative judge: one yes/no question per sentence to a decision model."""
import pytest

from living_reports.harness import decide, judge
from living_reports.metrics.runner import Fact


def fact(fid, label, formatted, metric="order_value"):
    return Fact(id=fid, label=label, value=1.0, formatted=formatted, metric=metric,
                metric_version=1, query_hash="h", snapshot="s1")


FACTS = {
    "q1_total.order_value": fact("q1_total.order_value", "Order value", "$870.8M"),
}
LABELS = {"q1_total.order_value": "Order value (total)"}


@pytest.fixture
def server(monkeypatch, store):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    sent = []

    def post(url, body, timeout):
        sent.append(body)
        return {"answers": {n: {"type": "noul", "noul": server.p.get(n, 0.99)} for n in body["questions"]}}

    server.sent, server.p = sent, {}
    monkeypatch.setattr(decide, "_post", post)
    return server


def test_each_sentence_is_one_question_with_values_written_out(server):
    texts = ["Order value", "The average order value is {fact:q1_total.order_value}. It grew."]
    judge.unsupported(texts, FACTS, LABELS, "What is the order value?", "t")
    body = server.sent[0]
    questions = [q["instructions"] for q in body["questions"].values()]
    assert len(questions) == 3  # the heading and two sentences
    assert any("The average order value is $870.8M [Order value (total)]." in q for q in questions)
    assert body["state"]["question"] == "What is the order value?"
    assert "$870.8M = Order value (total)" in body["state"]["values"][0]


def test_a_sentence_below_the_floor_is_returned(server):
    server.p = {"s2": 0.1}
    texts = ["Order value", "The average order value is {fact:q1_total.order_value}."]
    assert judge.unsupported(texts, FACTS, LABELS, "q", "t") == [
        "The average order value is {fact:q1_total.order_value}."
    ]


def test_no_judge_model_means_no_verdict(monkeypatch, store):
    monkeypatch.delenv("LR_DECIDE_MODEL", raising=False)
    monkeypatch.delenv("LR_JUDGE_MODEL", raising=False)
    assert judge.unsupported(["Fine."], FACTS, LABELS, "q", "t") is None


def test_the_rubric_asks_four_questions_about_the_whole_text(server):
    server.p = {"r1": 0.01, "r3": 0.01}  # "no" is the passing answer for these
    assert judge.rubric("brief", "text", "q?", has_action=True, turn_id="t") == []
    body = server.sent[0]
    assert set(body["questions"]) == {"r1", "r2", "r3", "r4"}
    assert body["state"] == {"question": "q?", "brief": "brief", "text": "text"}


def test_the_rubric_skips_the_action_item_without_an_action(server):
    judge.rubric("brief", "text", "q?", has_action=False, turn_id="t")
    assert set(server.sent[0]["questions"]) == {"r1", "r2", "r3"}


def test_a_failed_rubric_item_is_a_repair_note(server):
    # r1 must be "no": a high probability fails it. r2 must be "yes": a low one fails it.
    server.p = {"r1": 0.9, "r2": 0.1, "r3": 0.1}
    notes = judge.rubric("brief", "text", "q?", has_action=False, turn_id="t")
    assert len(notes) == 2
    assert "outside the cited claims" in notes[0] and "does not answer the question" in notes[1]


def test_no_judge_model_means_no_rubric(monkeypatch, store):
    monkeypatch.delenv("LR_DECIDE_MODEL", raising=False)
    monkeypatch.delenv("LR_JUDGE_MODEL", raising=False)
    assert judge.rubric("b", "t", "q", has_action=False, turn_id="t") is None
