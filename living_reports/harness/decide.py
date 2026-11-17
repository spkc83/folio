"""A client for decision models: typed questions on ``/v1/systemone``, probabilities back.

A decision model does not write text. It scores the options of each question in one pass.
Ollama (0.35 and later) and vLLM serve this endpoint. A model named ``gemini/...`` goes to the
Gemini Decisions API (``/v1beta/decisions``), which takes the same questions and returns the
same answers; the state goes as text in ``input``. Each step that uses this client is
optional: no model, or a failed call, returns ``None`` and the caller goes on without it.
"""
import json
import logging
import os
import threading
import urllib.request
from typing import Literal

from pydantic import BaseModel, ValidationError

from living_reports.config import get_settings
from living_reports.harness.llm import record_usage
from living_reports.harness.providers import ollama_base_url

log = logging.getLogger(__name__)

Role = Literal["decide", "judge", "rank"]
MAX_QUESTIONS = 64
MAX_OPTIONS = 26
TIMEOUT_SECONDS = 10  # a decision takes milliseconds; a hung server must not hold the route
WARM_TIMEOUT_SECONDS = 300  # a cold load takes much longer (d3:4b: about 75 s)
KEEP_ALIVE = "30m"
_loading: set[str] = set()
_loading_lock = threading.Lock()


class Answer(BaseModel):
    choice: str | None = None
    probabilities: dict[str, float] = {}
    noul: float | None = None
    confidence: float | None = None

    def top(self, n: int) -> list[tuple[str, float]]:
        return sorted(self.probabilities.items(), key=lambda kv: -kv[1])[:n]


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    if not 2 <= len(criteria) <= MAX_OPTIONS:
        raise ValueError(f"A choice needs 2 to {MAX_OPTIONS} options, not {len(criteria)}.")
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


def model_for(role: Role) -> str | None:
    settings = get_settings()
    if role == "judge":
        return settings.judge_model or settings.decide_model
    if role == "rank":
        return settings.rank_model or settings.decide_model
    return settings.decide_model


def _endpoint() -> str:
    base = get_settings().decide_base_url or ollama_base_url() or ""
    base = base.rstrip("/")
    base = base[: -len("/v1")] if base.endswith("/v1") else base
    return f"{base}/v1/systemone"


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/decisions"


def _gemini(model: str) -> bool:
    return model.lower().startswith("gemini/")


def _gemini_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _post(url: str, body: dict, timeout: float, headers: dict | None = None) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - set URL
        return json.loads(response.read())


def _wire_name(model: str) -> str:
    for prefix in ("ollama/", "ollama:", "systemone/"):
        if model.lower().startswith(prefix):
            return model[len(prefix):]
    return model


def _valid(answer: Answer, question: dict) -> bool:
    if question["type"] == "noul":
        return answer.noul is not None and 0.0 <= answer.noul <= 1.0
    options = set(question["criteria"])
    return answer.choice in options and set(answer.probabilities) <= options


def ask(state: dict, questions: dict[str, dict], role: Role, turn_id: str) -> dict[str, Answer] | None:
    """The answers, keyed by question name, or ``None`` when no decision is available.

    An answer that names an option the question did not offer is dropped."""
    model = model_for(role)
    if model is None or not questions:
        return None
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"A request takes at most {MAX_QUESTIONS} questions.")
    if _gemini(model):
        key = _gemini_key()
        if not key:
            log.warning("The decision model %s needs GOOGLE_API_KEY.", model)
            return None
        # The API accepts only these three fields; the state is text.
        body = {"model": model.split("/", 1)[1], "questions": questions,
                "input": "\n".join(f"{k}: {v}" for k, v in state.items())}
        call = lambda: _post(GEMINI_URL, body, TIMEOUT_SECONDS, headers={"x-goog-api-key": key})  # noqa: E731
    else:
        body = {"model": _wire_name(model), "state": state, "questions": questions, "keep_alive": KEEP_ALIVE}
        call = lambda: _post(_endpoint(), body, TIMEOUT_SECONDS)  # noqa: E731
    try:
        reply = call()
    except (OSError, ValueError) as error:  # URLError and HTTPError are OSErrors
        log.warning("The decision model %s did not answer: %s", model, error)
        warm([model])  # often a cold or unloaded model: load it for the next call
        return None
    usage = reply.get("usage") or {}
    record_usage(turn_id, model, role, usage.get("input_tokens", usage.get("total_input_tokens")),
                 usage.get("output_tokens", usage.get("total_output_tokens")))
    answers: dict[str, Answer] = {}
    for name, raw in (reply.get("answers") or {}).items():
        if name not in questions:
            continue
        try:
            answer = Answer.model_validate(raw)
        except ValidationError:
            continue
        if _valid(answer, questions[name]):
            answers[name] = answer
    return answers


def _load(model: str, post) -> None:
    """``post`` is bound when the load starts, so a later swap of ``_post`` does not apply."""
    body = {
        "model": _wire_name(model),
        "state": {"text": "ready"},
        "questions": {"ready": noul("Is the text a word?")},
        "keep_alive": KEEP_ALIVE,
    }
    try:
        post(_endpoint(), body, WARM_TIMEOUT_SECONDS)
        log.info("The decision model %s is loaded.", model)
    except (OSError, ValueError) as error:
        log.warning("Folio could not load the decision model %s: %s", model, error)
    finally:
        with _loading_lock:
            _loading.discard(model)


def warm(models: list[str] | None = None) -> list[threading.Thread]:
    """Load the decision and judge models in the background, so that a cold load does not
    exceed ``TIMEOUT_SECONDS`` on a real call. A model that is loading is not loaded again.

    ``models`` defaults to the decision model and the judge."""
    wanted = models if models is not None else [model_for("decide"), model_for("judge"), model_for("rank")]
    threads = []
    for model in dict.fromkeys(m for m in wanted if m and not _gemini(m)):  # a hosted model is warm
        with _loading_lock:
            if model in _loading:
                continue
            _loading.add(model)
        thread = threading.Thread(target=_load, args=(model, _post), name=f"warm {model}", daemon=True)
        thread.start()
        threads.append(thread)
    return threads
