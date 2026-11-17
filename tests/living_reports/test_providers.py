from types import SimpleNamespace

from living_reports.harness import providers as llm_client


class Fake:
    last_usage = (11, 22)

    def generate(self, prompt, **kwargs):
        return "hello"


class Plain:
    def generate(self, prompt, **kwargs):
        return "hello"


def test_usage_is_copied_from_the_backend(monkeypatch):
    monkeypatch.setattr(llm_client, "_make_backend", lambda p: Fake())
    r = llm_client.generate("x", provider="openai", model="m")
    assert (r.input_tokens, r.output_tokens) == (11, 22)


def test_backend_without_usage_gives_none(monkeypatch):
    monkeypatch.setattr(llm_client, "_make_backend", lambda p: Plain())
    r = llm_client.generate("x", provider="openai", model="m")
    assert r.text == "hello" and r.input_tokens is None and r.output_tokens is None


def test_openai_backend_records_usage():
    backend = llm_client._OpenAIBackend.__new__(llm_client._OpenAIBackend)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
    )
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: response))
    )
    assert backend.generate("x", model="m") == "ok"
    assert backend.last_usage == (3, 4)


def test_anthropic_backend_records_usage():
    backend = llm_client._AnthropicBackend.__new__(llm_client._AnthropicBackend)
    message = SimpleNamespace(
        content=[SimpleNamespace(text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=6),
    )
    backend._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: message))
    assert backend.generate("x", model="m") == "ok"
    assert backend.last_usage == (5, 6)


def test_google_backend_records_usage():
    backend = llm_client._GoogleBackend.__new__(llm_client._GoogleBackend)
    response = SimpleNamespace(
        text="ok",
        usage_metadata=SimpleNamespace(prompt_token_count=7, candidates_token_count=8),
    )
    backend._client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **k: response))
    assert backend.generate("x", model="m") == "ok"
    assert backend.last_usage == (7, 8)


class Recording:
    def __init__(self):
        self.kwargs = None

    def generate(self, prompt, **kwargs):
        self.kwargs = kwargs
        return "{}"


def test_generate_forwards_a_json_schema_to_the_backend(monkeypatch):
    backend = Recording()
    monkeypatch.setattr(llm_client, "_make_backend", lambda p: backend)
    llm_client.generate("x", provider="ollama", model="m", json_schema={"type": "object"})
    assert backend.kwargs["json_schema"] == {"type": "object"}


def test_generate_sends_no_schema_key_when_none_is_given(monkeypatch):
    backend = Recording()
    monkeypatch.setattr(llm_client, "_make_backend", lambda p: backend)
    llm_client.generate("x", provider="openai", model="m")
    assert "json_schema" not in backend.kwargs


def _openai_backend(sent):
    backend = llm_client._OpenAIBackend.__new__(llm_client._OpenAIBackend)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))], usage=None
    )

    def create(**kwargs):
        sent.update(kwargs)
        return response

    backend._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return backend


def test_openai_backend_sends_the_schema_as_response_format():
    sent = {}
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    _openai_backend(sent).generate("x", model="m", json_schema=schema)
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "output", "schema": schema},
    }


def test_openai_backend_without_schema_sends_no_response_format():
    sent = {}
    _openai_backend(sent).generate("x", model="m")
    assert "response_format" not in sent


def test_backends_without_schema_support_accept_and_ignore_it():
    anthropic = llm_client._AnthropicBackend.__new__(llm_client._AnthropicBackend)
    message = SimpleNamespace(content=[SimpleNamespace(text="ok")], usage=None)
    anthropic._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: message))
    assert anthropic.generate("x", model="m", json_schema={"type": "object"}) == "ok"


def test_a_reasoning_model_gets_completion_tokens_minimal_effort_and_no_temperature():
    # gpt-5 and o-series models reject max_tokens and temperature, and their reasoning
    # tokens count against the cap: minimal effort leaves the cap for the JSON.
    sent = {}
    _openai_backend(sent).generate("x", model="gpt-5-nano", max_tokens=2048)
    assert sent["max_completion_tokens"] == 2048 and "max_tokens" not in sent
    assert "temperature" not in sent and sent["reasoning_effort"] == "minimal"


def test_a_gpt_6_model_gets_no_reasoning_and_keeps_its_temperature():
    # gpt-6 models reject max_tokens and the effort "minimal"; "none" accepts a temperature.
    sent = {}
    _openai_backend(sent).generate("x", model="gpt-6-luna", max_tokens=2048)
    assert sent["max_completion_tokens"] == 2048 and "max_tokens" not in sent
    assert sent["reasoning_effort"] == "none" and sent["temperature"] == 0.0


def test_a_chat_model_keeps_max_tokens_and_temperature():
    sent = {}
    _openai_backend(sent).generate("x", model="gpt-4o-mini", max_tokens=2048)
    assert sent["max_tokens"] == 2048 and sent["temperature"] == 0.0
    assert "reasoning_effort" not in sent


def _google_backend(sent, usage=None):
    backend = llm_client._GoogleBackend.__new__(llm_client._GoogleBackend)
    response = SimpleNamespace(text="{}", usage_metadata=usage)

    def generate_content(**kwargs):
        sent.update(kwargs)
        return response

    backend._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return backend


def test_google_backend_sends_the_schema_as_json_output():
    sent = {}
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    _google_backend(sent).generate("x", model="gemini-3.1-flash-lite", json_schema=schema)
    config = sent["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema


def test_google_backend_counts_thinking_tokens_as_output():
    # Google bills thinking tokens at the output price.
    usage = SimpleNamespace(prompt_token_count=7, candidates_token_count=8, thoughts_token_count=30)
    backend = _google_backend({}, usage)
    backend.generate("x", model="gemini-3.1-flash-lite")
    assert backend.last_usage == (7, 38)
