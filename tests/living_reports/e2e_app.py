"""Test-only server for the demo browser test on the retail demo. A deterministic stand-in
replaces the model."""
import json
import re

from living_reports.harness.providers import LLMResponse
from living_reports.api.app import create_app
from living_reports.harness import llm


def _route(prompt: str) -> dict:
    question = re.search(r"<question>\n(.*?)\n</question>", prompt, re.S).group(1).lower()
    base = {"clarify": None, "assumptions": [], "out_of_scope": None}
    if "doing" in question:
        return {**base, "objective": "status",
                "query": {"metrics": ["gross_margin"], "dimensions": ["sales.channel"]}}
    return {**base, "objective": "why",
            "query": {"metrics": ["revenue"], "dimensions": ["products.category"]}}


def _narrate(prompt: str) -> dict:
    """The section total as a value, then the first two claims of the brief."""
    ref, metric = re.search(r"^- (q\d+)\.value\.(\w+): ", prompt, re.M).groups()
    total = f"{ref}_total.{metric}"
    claims = re.findall(r"^- (q\d+[\w.\-+]*): ", prompt, re.M)[:2]
    return {"title": "Revenue by category",
            "heading": "Where revenue comes from",
            "paragraphs": [f"Overall, {{fact:{total}}}. " + " ".join(f"{{claim:{c}}}." for c in claims)],
            "action": None}


def _reply(prompt: str) -> dict:
    token = re.search(r"Selected value: ([\w.\-+]+) =", prompt).group(1)
    return {"reply": f"The selected value is {{fact:{token}}}."}


def fake_generate(prompt: str, **kwargs) -> LLMResponse:
    if "You map a business question" in prompt:
        body = _route(prompt)
    elif "You write the words" in prompt:
        body = _narrate(prompt)
    elif "You answer a question about one part" in prompt:
        body = _reply(prompt)
    else:
        body = {"claims": []}
    return LLMResponse(json.dumps(body), "fake-small", "openai", 900, 80)


def create():
    llm._generate = fake_generate
    return create_app()
