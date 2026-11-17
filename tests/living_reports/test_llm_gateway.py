import pytest
from pydantic import BaseModel

from living_reports.harness.providers import LLMResponse
from living_reports.harness import llm


class Answer(BaseModel):
    value: int


class Script:
    """Stands in for the provider call and replays outputs."""

    def __init__(self, *outputs, tokens=(100, 20)):
        self.outputs, self.tokens, self.prompts, self.schemas, self.kwargs = list(outputs), tokens, [], [], []

    def __call__(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.schemas.append(kwargs.get("json_schema"))
        self.kwargs.append(kwargs)
        out = self.outputs.pop(0)
        if out is None:
            return None
        return LLMResponse(out, kwargs.get("model") or "m", "openai", *self.tokens)


@pytest.fixture
def script(orders_file, monkeypatch):
    def use(*outputs, **kw):
        s = Script(*outputs, **kw)
        monkeypatch.setattr(llm, "_generate", s)
        return s
    return use


def test_plain_json(script):
    script('{"value": 3}')
    assert llm.call_json("p", Answer, "small", "t1").value == 3


def test_json_in_a_code_fence(script):
    script('Here it is:\n```json\n{"value": 4}\n```')
    assert llm.call_json("p", Answer, "small", "t1").value == 4


def test_invalid_output_gets_a_repair_prompt(script):
    s = script("not json", '{"value": 5}')
    assert llm.call_json("p", Answer, "small", "t1").value == 5
    assert "valid JSON" in s.prompts[1]


def test_three_invalid_outputs_raise(script):
    script("a", "b", "c")
    with pytest.raises(llm.InvalidModelOutput):
        llm.call_json("p", Answer, "small", "t1")


def test_wrong_shape_is_invalid(script):
    script('{"value": "x"}', '{"nope": 1}', "[]")
    with pytest.raises(llm.InvalidModelOutput):
        llm.call_json("p", Answer, "small", "t1")


def test_no_provider_raises_unavailable(script):
    script(None)
    with pytest.raises(llm.ModelUnavailable):
        llm.call_json("p", Answer, "small", "t1")


def test_ledger_sums_a_turn_including_repairs(script):
    script("bad", '{"value": 1}', tokens=(100, 20))
    llm.call_json("p", Answer, "small", "t2")
    cost = llm.turn_cost("t2")
    assert (cost.input_tokens, cost.output_tokens, cost.calls) == (200, 40, 2)


def test_ledger_separates_turns(script):
    script('{"value": 1}', '{"value": 2}')
    llm.call_json("p", Answer, "small", "a")
    llm.call_json("p", Answer, "small", "b")
    assert llm.turn_cost("a").calls == 1


def test_unknown_price_is_none_not_zero(script, monkeypatch):
    monkeypatch.setenv("LR_SMALL_MODEL", "mystery-model")
    script('{"value": 1}')
    llm.call_json("p", Answer, "small", "t3")
    assert llm.turn_cost("t3").usd is None


def test_known_price_gives_usd(script, monkeypatch):
    monkeypatch.setenv("LR_SMALL_MODEL", "ollama/qwen")
    script('{"value": 1}')
    llm.call_json("p", Answer, "small", "t4")
    assert llm.turn_cost("t4").usd == 0.0


def test_empty_turn_costs_nothing(orders_file):
    cost = llm.turn_cost("never")
    assert cost.calls == 0 and cost.usd == 0.0


def test_missing_token_counts_make_the_cost_unknown(script, monkeypatch):
    monkeypatch.setenv("LR_SMALL_MODEL", "ollama/qwen")
    script('{"value": 1}', tokens=(None, None))
    llm.call_json("p", Answer, "small", "t5")
    assert llm.turn_cost("t5").usd is None


def test_call_json_sends_the_model_schema(script):
    s = script('{"value": 1}')
    llm.call_json("p", Answer, "small", "t")
    assert s.schemas[0] == llm.bound_schema(Answer.model_json_schema())
    assert "title" not in s.schemas[0]


def test_call_json_sends_a_narrower_schema_when_given(script):
    s = script('{"value": 1}')
    narrow = {"type": "object", "properties": {"value": {"enum": [1]}}}
    llm.call_json("p", Answer, "small", "t", json_schema=narrow)
    assert s.schemas[0] == narrow


class Wordy(BaseModel):
    title: str
    lines: list[str]
    kind: str = "a"


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_wire_schema_bounds_every_array_and_string(script):
    s = script('{"title": "t", "lines": ["a"]}')
    llm.call_json("p", Wordy, "small", "t")
    nodes = list(_walk(s.schemas[0]))
    arrays = [n for n in nodes if n.get("type") == "array"]
    strings = [n for n in nodes if n.get("type") == "string"]
    assert arrays and all("maxItems" in n for n in arrays)
    assert strings and all("maxLength" in n for n in strings)


def test_bounds_keep_enums_and_tighter_limits():
    schema = {"type": "object", "properties": {
        "pick": {"type": "string", "enum": ["a", "b"]},
        "few": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 5}},
    }}
    out = llm.bound_schema(schema)
    assert "maxLength" not in out["properties"]["pick"]
    assert out["properties"]["few"]["maxItems"] == 2
    assert out["properties"]["few"]["items"]["maxLength"] == 5


def test_bounds_stay_under_the_llama_cpp_repetition_limit():
    # llama.cpp fails with "failed to parse grammar" when a repetition count reaches 2000.
    schema = {"type": "object", "properties": {
        "free": {"type": "string"},
        "long": {"type": "string", "maxLength": 5000},
        "many": {"type": "array", "maxItems": 3000, "items": {"type": "integer"}},
    }}
    out = llm.bound_schema(schema)["properties"]
    assert out["free"]["maxLength"] < llm.GRAMMAR_REPEAT_LIMIT
    assert out["long"]["maxLength"] < llm.GRAMMAR_REPEAT_LIMIT
    assert out["many"]["maxItems"] < llm.GRAMMAR_REPEAT_LIMIT


def test_call_json_caps_output_tokens(script):
    s = script('{"value": 1}')
    llm.call_json("p", Answer, "small", "t")
    assert 0 < s.kwargs[0]["max_tokens"] <= 4096


def test_the_wire_schema_has_no_title_annotations(script):
    # A model copied the class name "_Narration" into the report title. A property that is
    # named "title" stays; only the annotation strings go.
    s = script('{"title": "t", "lines": ["a"]}')
    llm.call_json("p", Wordy, "small", "t")
    nodes = list(_walk(s.schemas[0]))
    assert not any(isinstance(n.get("title"), str) for n in nodes)
    assert "title" in s.schemas[0]["properties"]
