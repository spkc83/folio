"""Follow-up chips for a section: code makes every candidate; a decision model can order them.

Each candidate is a governed query that compiles against the semantic model. The decision
model chooses which next view most likely explains the section's top claim. It never writes a
query or a word: it only orders the candidates that code made. The ranking is off by default
(``LR_RANK_FOLLOW_UPS``): the order is then the vocabulary order.
"""
from typing import Any

from living_reports.config import get_settings
from living_reports.engine import OutOfScope, Query, TimeRange
from living_reports.engine.compile import compile
from living_reports.engine.query import Filter, flat_filters
from living_reports.harness import decide
from living_reports.semantic.projections import vocabulary_of
from living_reports.spec.models import FollowUp

MAX_FOLLOW_UPS = 3
_NEXT = ("Which next view of the data most likely explains the finding, or shows where it "
         "comes from, for the person who asked the question?")


def _query(query: Query, **update: Any) -> dict[str, Any]:
    return query.model_copy(update=update).model_dump(mode="json", exclude_none=True)


def _compiles(definition: dict[str, Any], model) -> bool:
    try:
        compile(Query.model_validate(definition), model)
    except (OutOfScope, ValueError):
        return False
    return True


def candidates(query: Query, model, top: tuple[str, str] | None = None) -> list[FollowUp]:
    """Every follow-up that fits the section, in vocabulary order.

    ``top`` is the section's top member and its dimension, such as ``("App", CHANNEL)``: it
    adds "App by <dimension>" for each other dimension."""
    vocabulary = vocabulary_of(model)
    used = {*query.dimensions, *(f.member for f in flat_filters(query.filters))}
    unused = [t for t in vocabulary.dimensions if not t.is_time and t.id not in used]
    out = [FollowUp(label=f"By {t.label}", query=_query(query, dimensions=[t.id])) for t in unused]
    if not query.is_time_series:
        time = (query.time.model_copy(update={"grain": "month"}) if query.time
                else TimeRange(grain="month"))
        out.append(FollowUp(label="Over time", query=_query(query, dimensions=[], time=time)))
    if top is not None:
        value, member = top
        narrowed = query.model_copy(update={"filters": [
            *query.filters, Filter(member=member, op="eq", values=[value])]})
        out += [FollowUp(label=f"{value} by {t.label}", query=_query(narrowed, dimensions=[t.id]))
                for t in unused]
    found = [c for c in out if _compiles(c.query, model)]
    return list({c.label: c for c in found}.values())[: decide.MAX_OPTIONS]


def rank(found: list[FollowUp], question: str, finding: str, turn_id: str) -> list[FollowUp]:
    """The candidates that most likely explain ``finding``, best first."""
    if len(found) < 2 or not get_settings().rank_follow_ups:
        return found[:MAX_FOLLOW_UPS]
    options = {chr(ord("a") + i): c.label for i, c in enumerate(found)}
    answers = decide.ask({"question": question, "finding": finding},
                         {"next": decide.choice(_NEXT, options)}, "rank", turn_id)
    answer = (answers or {}).get("next")
    if answer is None:
        return found[:MAX_FOLLOW_UPS]
    p = answer.probabilities
    order = sorted(range(len(found)), key=lambda i: -p.get(chr(ord("a") + i), 0.0))
    return [found[i] for i in order][:MAX_FOLLOW_UPS]


def follow_ups(question: str, query: Query, claims: list, facts: dict, model,
               turn_id: str) -> list[FollowUp]:
    """The ranked follow-ups of a new section. ``claims`` are its candidates, most salient first."""
    from living_reports.harness.claims import readable

    leader = next((c for c in claims if ".extreme.max." in c.id), None)
    top = None
    if leader is not None and len(query.dimensions) == 1 and query.dimensions[0] in leader.segment:
        top = (leader.segment[query.dimensions[0]], query.dimensions[0])
    finding = readable(claims[0].text, facts) if claims else question
    return rank(candidates(query, model, top), question, finding, turn_id)
