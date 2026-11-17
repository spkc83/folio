"""The Gemini Decisions API as a decision model backend (``LR_DECIDE_MODEL=gemini/gemini-decision-1``).

No test calls Google: a fake transport checks the request and returns the documented shape.
"""
import pytest

from living_reports.harness import decide


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("LR_DECIDE_MODEL", "gemini/gemini-decision-1")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    sent = []

    def post(url, body, timeout, headers=None):
        sent.append({"url": url, "body": body, "headers": headers or {}})
        return {"answers": {
                    "metric": {"type": "choice", "choice": "a", "probabilities": {"a": 0.8, "b": 0.2},
                               "confidence": 0.8},
                    "answerable": {"type": "noul", "noul": 0.9, "confidence": 0.9}},
                "usage": {"total_input_tokens": 120, "total_output_tokens": 0, "total_tokens": 120}}

    monkeypatch.setattr(decide, "_post", post)
    return sent


QUESTIONS = {"metric": decide.choice("Which metric?", {"a": "Revenue", "b": "Units"}),
             "answerable": decide.noul("Can the data answer the question?")}


def test_a_gemini_request_goes_to_the_decisions_endpoint_with_the_key_header(gemini):
    answers = decide.ask({"question": "How is revenue doing?"}, QUESTIONS, "decide", "t")
    (call,) = gemini
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/decisions"
    assert call["headers"] == {"x-goog-api-key": "test-key"}
    assert call["body"]["model"] == "gemini-decision-1"
    assert call["body"]["input"] == "question: How is revenue doing?"
    assert call["body"]["questions"] == QUESTIONS
    assert set(call["body"]) == {"model", "input", "questions"}  # any other field is a 400
    assert answers["metric"].choice == "a" and answers["answerable"].noul == 0.9


def test_a_gemini_call_records_its_input_tokens(gemini, monkeypatch):
    recorded = []
    monkeypatch.setattr(decide, "record_usage", lambda *a: recorded.append(a))
    decide.ask({"question": "q"}, QUESTIONS, "decide", "t")
    assert recorded == [("t", "gemini/gemini-decision-1", "decide", 120, 0)]


def test_without_a_key_gemini_gives_no_decision(gemini, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert decide.ask({"question": "q"}, QUESTIONS, "decide", "t") is None
    assert gemini == []


def test_a_gemini_model_needs_no_warm_up(gemini):
    assert decide.warm(["gemini/gemini-decision-1"]) == []
    assert gemini == []


def test_the_price_of_gemini_decision_1_is_known():
    import yaml

    from living_reports.harness.llm import _PRICES, _price
    price = _price("gemini/gemini-decision-1", yaml.safe_load(_PRICES.read_text())["models"])
    assert price == {"input": 0.10, "output": 0.0}  # output tokens are not billed
