"""Assemble a small context for one turn.

There is no memory store. Context comes from the anchored section, its linked graph
nodes, and the current thread. Other sections and closed transcripts stay out.
"""
from pydantic import BaseModel

from living_reports.harness.claims import render_claims
from living_reports.reports.store import StoredReport
from living_reports.semantic.graph import FolioGraph
from living_reports.spec.models import Prose, Section, Tile
from living_reports.spec.validate import TOKEN

CHARS_PER_TOKEN = 4
MAX_FACTS = 30


class ContextPack(BaseModel):
    text: str
    estimated_tokens: int


def _fact_ids(section: Section, report: StoredReport) -> list[str]:
    ids: list[str] = []
    refs = []
    for block in section.blocks:
        if isinstance(block, Prose):
            ids += TOKEN.findall(render_claims(block.text, report.spec.claims))
        elif isinstance(block, Tile):
            ids.append(block.fact_id)
        elif ref := getattr(block, "query_ref", None):
            refs.append(ref)
    for ref in refs:
        ids += [f for f in report.facts if f.startswith(f"{ref}.") or f.startswith(f"{ref}_total.")]
    return list(dict.fromkeys(i for i in ids if i in report.facts))[:MAX_FACTS]


def build_context(
    report: StoredReport,
    section_id: str,
    fact_id: str | None,
    graph: FolioGraph,
    messages: list,
    budget_tokens: int = 2000,
) -> ContextPack:
    section = next((s for s in report.spec.sections if s.id == section_id), None)
    if section is None:
        raise KeyError(section_id)
    ids = _fact_ids(section, report)
    lines = [f"Report: {report.spec.title}", f"Section: {section.heading}"]
    lines.append(f"Intent: {section.intent}")
    lines += [f"Text: {b.text}" for b in section.blocks if isinstance(b, Prose)]
    lines += [f"Decision: {d}" for d in section.decisions]
    if fact_id and fact_id in report.facts:
        fact = report.facts[fact_id]
        lines.append(f"Selected value: {fact_id} = {fact.label}: {fact.formatted}")
    lines += [f"{i} = {report.facts[i].label}: {report.facts[i].formatted}" for i in ids]
    metrics = dict.fromkeys(report.facts[i].metric for i in ids if report.facts[i].metric)
    terms = {t.id: t for t in graph.vocabulary().metrics} if metrics else {}
    for metric in metrics:
        if metric in terms:
            lines.append(f"Definition of {metric}: {terms[metric].definition}")

    limit = budget_tokens * CHARS_PER_TOKEN
    text = "\n".join(lines)[:limit]
    history: list[str] = []
    room = limit - len(text)
    for message in reversed(messages):
        role, body = (
            (message["role"], message["text"])
            if isinstance(message, dict)
            else (message.role, message.text)
        )
        entry = f"{role}: {body}"[: max(room - 1, 0)]
        if not entry:
            break
        history.insert(0, entry)
        room -= len(entry) + 1
    if history:
        text = "\n".join([text, *history])
    return ContextPack(text=text, estimated_tokens=-(-len(text) // CHARS_PER_TOKEN))
