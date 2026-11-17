"""Tiered LLM calls with validated JSON output and a usage ledger."""
import fnmatch
import re
from pathlib import Path
from typing import Literal, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from living_reports.config import get_settings
from living_reports.harness.providers import generate as _generate
from living_reports.store import get_store

Tier = Literal["small", "frontier", "checker"]
Model = TypeVar("Model", bound=BaseModel)

_PRICES = Path(__file__).parent.parent / "pricing.yaml"


class ModelUnavailable(RuntimeError):
    pass


class InvalidModelOutput(RuntimeError):
    pass


class TurnCost(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float | None = 0.0
    calls: int = 0
    models: list[str] = []
    rung: str | None = None


def _model_for(tier: Tier) -> str | None:
    settings = get_settings()
    return {
        "small": settings.small_model,
        "frontier": settings.frontier_model,
        "checker": settings.checker_model,
    }[tier]


def record_usage(turn_id: str, model: str, tier: str, in_tokens, out_tokens) -> None:
    """One model call in the usage ledger. ``tier`` is the tier or the role of the call."""
    store = get_store()
    store.execute(
        "INSERT INTO llm_usage (turn_id, model, tier, input_tokens, output_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [turn_id, model, tier, in_tokens, out_tokens, store.ts(store.now())],
    )


def escape_data(text: str) -> str:
    """Stop a value from closing the fence around it in a prompt."""
    return text.replace("<", "\u2039")


def _extract_json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return fenced.group(1) if fenced else text


def inline_refs(schema: dict) -> dict:
    """Replace each local $ref with its definition, so a grammar converter sees one tree."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


MAX_ITEMS = 12
MAX_LENGTH = 1000
MAX_OUTPUT_TOKENS = 2048
# llama.cpp (Ollama) fails with "failed to parse grammar" when a repetition count
# reaches this value, so every bound on the wire stays below it.
GRAMMAR_REPEAT_LIMIT = 2000


def bound_schema(schema: dict, max_items: int = MAX_ITEMS, max_length: int = MAX_LENGTH) -> dict:
    """Give each array and free-text string a limit.

    Constrained decoding forces valid JSON but not short JSON: without a limit, a small
    model can fill one array or string until the token cap stops it."""
    if isinstance(schema, list):
        return [bound_schema(v, max_items, max_length) for v in schema]
    if not isinstance(schema, dict):
        return schema
    # A "title" annotation (a string) names the class, such as "_Narration", and a model can
    # copy it into its answer. A property named "title" is a dict under "properties" and stays.
    node = {k: bound_schema(v, max_items, max_length) for k, v in schema.items()
            if not (k == "title" and isinstance(v, str))}
    cap = GRAMMAR_REPEAT_LIMIT - 1
    if node.get("type") == "array":
        node["maxItems"] = min(node.get("maxItems", max_items), cap)
    if node.get("type") == "string" and "enum" not in node:
        node["maxLength"] = min(node.get("maxLength", max_length), cap)
    return node


def call_json(
    prompt: str,
    schema: type[Model],
    tier: Tier,
    turn_id: str,
    max_repairs: int = 2,
    json_schema: dict | None = None,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> Model:
    """Return validated output, or raise. Each repair attempt counts in the ledger.

    The provider gets a JSON schema for constrained decoding. Validation still runs,
    because some providers ignore the schema."""
    current = prompt
    problem = ""
    wire_schema = bound_schema(json_schema or inline_refs(schema.model_json_schema()))
    for _ in range(max_repairs + 1):
        response = _generate(
            current, model=_model_for(tier), json_schema=wire_schema, max_tokens=max_tokens
        )
        if response is None:
            raise ModelUnavailable("No language model is available.")
        record_usage(turn_id, response.model, tier, response.input_tokens, response.output_tokens)
        try:
            return schema.model_validate_json(_extract_json(response.text))
        except ValidationError as error:
            problem = str(error)[:600]
        current = (
            f"{prompt}\n\nYour last output was rejected. Return only valid JSON for the "
            f"requested schema.\nThe problem was:\n{problem}"
        )
    raise InvalidModelOutput(problem)


def _price(model: str, prices: dict) -> dict | None:
    if model in prices:
        return prices[model]
    return next((p for k, p in prices.items() if fnmatch.fnmatch(model, k)), None)


def turn_cost(turn_id: str) -> TurnCost:
    rows = get_store().rows(
        "SELECT model, input_tokens, output_tokens FROM llm_usage WHERE turn_id = ?",
        [turn_id],
    )
    prices = yaml.safe_load(_PRICES.read_text())["models"]
    usd: float | None = 0.0
    for model, in_tokens, out_tokens in rows:
        price = _price(model, prices)
        if price is None or in_tokens is None or out_tokens is None:
            usd = None
        elif usd is not None:
            usd += (in_tokens * price["input"] + out_tokens * price["output"]) / 1_000_000
    return TurnCost(
        input_tokens=sum(r[1] or 0 for r in rows),
        output_tokens=sum(r[2] or 0 for r in rows),
        usd=usd,
        calls=len(rows),
        models=sorted({r[0] for r in rows}),
    )
