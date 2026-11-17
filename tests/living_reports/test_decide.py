"""The decision-model client: typed questions on /v1/systemone, probabilities back."""
import pytest

from living_reports.harness import decide


@pytest.fixture
def server(monkeypatch, store):
    calls = []

    def post(url, body, timeout):
        calls.append((url, body))
        if server.reply is None:
            raise OSError("connection refused")
        return server.reply

    server.calls, server.reply = calls, None
    monkeypatch.setattr(decide, "_post", post)
    return server


QUESTIONS = {
    "metric": decide.choice("Which metric?", {"fraud_loss": "Loss", "fraud_rate": "Rate"}),
    "answerable": decide.noul("Can the metrics answer it?"),
}
REPLY = {
    "model": "pplx-decider-flash:4b",
    "answers": {
        "metric": {"type": "choice", "choice": "fraud_loss",
                   "probabilities": {"fraud_loss": 0.9, "fraud_rate": 0.1}, "confidence": 0.8},
        "answerable": {"type": "noul", "noul": 0.97},
    },
    "usage": {"input_tokens": 300, "output_tokens": 2},
}


def test_no_decision_model_means_no_call(server, monkeypatch):
    monkeypatch.delenv("LR_DECIDE_MODEL", raising=False)
    assert decide.ask({"question": "q"}, QUESTIONS, "decide", "t") is None
    assert server.calls == []


def test_a_request_goes_to_the_systemone_endpoint_without_the_provider_prefix(server, monkeypatch):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/pplx-decider-flash:4b")
    monkeypatch.setenv("LR_DECIDE_BASE_URL", "http://gpu:11434")
    server.reply = REPLY
    answers = decide.ask({"question": "q"}, QUESTIONS, "decide", "t")
    url, body = server.calls[0]
    assert url == "http://gpu:11434/v1/systemone"
    assert body["model"] == "pplx-decider-flash:4b" and body["state"] == {"question": "q"}
    assert body["questions"]["metric"]["criteria"] == {"fraud_loss": "Loss", "fraud_rate": "Rate"}
    assert answers["metric"].choice == "fraud_loss" and answers["metric"].top(2) == [
        ("fraud_loss", 0.9), ("fraud_rate", 0.1)]
    assert answers["answerable"].noul == 0.97


def test_the_default_server_is_the_ollama_server(server, monkeypatch):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    monkeypatch.setenv("LR_OLLAMA_BASE_URL", "http://box:11434/v1")
    server.reply = REPLY
    decide.ask({"question": "q"}, QUESTIONS, "decide", "t")
    assert server.calls[0][0] == "http://box:11434/v1/systemone"


def test_usage_goes_to_the_ledger_under_the_role(server, monkeypatch, store):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    server.reply = REPLY
    decide.ask({"question": "q"}, QUESTIONS, "judge", "turn-9")
    rows = store.rows("SELECT model, tier, input_tokens, output_tokens FROM llm_usage WHERE turn_id = 'turn-9'")
    assert rows == [("ollama/d3:4b", "judge", 300, 2)]


def test_a_failed_call_returns_none_so_the_route_goes_on(server, monkeypatch, caplog):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    assert decide.ask({"question": "q"}, QUESTIONS, "decide", "t") is None
    assert "decision model" in caplog.text


def test_an_answer_with_an_unknown_option_is_ignored(server, monkeypatch):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    server.reply = {"answers": {"metric": {"type": "choice", "choice": "profit",
                                           "probabilities": {"profit": 1.0}}}}
    assert "metric" not in decide.ask({"question": "q"}, QUESTIONS, "decide", "t")


def test_the_judge_defaults_to_the_decision_model(monkeypatch):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    monkeypatch.delenv("LR_JUDGE_MODEL", raising=False)
    assert decide.model_for("judge") == "ollama/d3:4b"
    monkeypatch.setenv("LR_JUDGE_MODEL", "ollama/nimble:9b")
    assert decide.model_for("judge") == "ollama/nimble:9b"
    assert decide.model_for("decide") == "ollama/d3:4b"


@pytest.mark.parametrize("count", [1, 27])
def test_a_choice_needs_two_to_twenty_six_options(count):
    with pytest.raises(ValueError):
        decide.choice("Which?", {f"o{i}": "x" for i in range(count)})


# Warm-up: a cold load (about 75 s for d3:4b) is longer than the request timeout. ----------


def _join(threads):
    for t in threads:
        t.join(5)


def test_warm_loads_each_model_once_with_a_long_timeout(monkeypatch):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    monkeypatch.setenv("LR_JUDGE_MODEL", "ollama/d3:9b")
    sent = []
    monkeypatch.setattr(decide, "_post", lambda url, body, timeout: sent.append((body, timeout)) or {})
    _join(decide.warm())
    assert sorted(b["model"] for b, _ in sent) == ["d3:4b", "d3:9b"]
    assert all(t == decide.WARM_TIMEOUT_SECONDS > decide.TIMEOUT_SECONDS for _, t in sent)
    assert all(b["keep_alive"] for b, _ in sent)


def test_a_judge_that_is_the_decision_model_loads_once(monkeypatch):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    monkeypatch.delenv("LR_JUDGE_MODEL", raising=False)
    sent = []
    monkeypatch.setattr(decide, "_post", lambda url, body, timeout: sent.append(body) or {})
    _join(decide.warm())
    assert len(sent) == 1


def test_no_decision_model_means_no_warm_up(monkeypatch):
    monkeypatch.delenv("LR_DECIDE_MODEL", raising=False)
    monkeypatch.delenv("LR_JUDGE_MODEL", raising=False)
    assert decide.warm() == []


def test_a_model_that_is_loading_is_not_loaded_again(monkeypatch):
    import threading
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    release, sent = threading.Event(), []

    def slow(url, body, timeout):
        sent.append(body)
        release.wait(5)
        return {}

    monkeypatch.setattr(decide, "_post", slow)
    first = decide.warm()
    second = decide.warm()
    release.set()
    _join(first + second)
    assert len(first) == 1 and second == [] and len(sent) == 1


def test_a_failed_warm_up_is_logged_and_can_run_again(monkeypatch, caplog):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")

    def down(url, body, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(decide, "_post", down)
    _join(decide.warm())
    assert "could not load" in caplog.text
    assert len(decide.warm()) == 1


def test_a_failed_call_starts_a_warm_up_for_the_next_call(server, monkeypatch):
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")
    started = []
    monkeypatch.setattr(decide, "warm", lambda models=None: started.append(models) or [])
    assert decide.ask({"question": "q"}, QUESTIONS, "decide", "t") is None
    assert started == [["ollama/d3:4b"]]


def test_the_app_warms_the_decision_models_when_it_starts(monkeypatch, store):
    from fastapi.testclient import TestClient

    from living_reports.api.app import create_app
    started = []
    monkeypatch.setattr(decide, "warm", lambda models=None: started.append(models) or [])
    with TestClient(create_app()):
        pass
    assert started == [None]
