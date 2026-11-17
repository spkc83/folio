"""The insight eval: does a report surface the stories that the demo data plants?

A story is surfaced when a claim that the report cites matches one of the story's rules. A
rule reads the claim's kind, segment, and metric, and a direction that code computes from the
claim's facts. It never reads the claim's words.

Run: python -m living_reports.evals.insights --arm writer --runs 3 --out insights.json
"""
import argparse
import json
from pathlib import Path
from typing import Any, Literal
from unittest import mock

import yaml
from pydantic import BaseModel, ConfigDict

from living_reports.harness.claims import FLAT, _steps, cited_claims
from living_reports.metrics.runner import Fact
from living_reports.spec.models import Claim, ClaimKind

Level = Literal["direct", "topic", "open"]


class StoryRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: list[ClaimKind]
    metric: str
    # The claim's segment holds each of these members, with these values. More is allowed.
    segment: dict[str, str] = {}
    direction: Literal["up", "down"] | None = None
    misses: bool | None = None  # a target claim: the value misses the target
    compare_a: str | None = None  # a compare claim: the larger member


class Story(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    # False when no claim kind can state the story: it is never surfaced, and it names a gap.
    expressible: bool = True
    any_of: list[StoryRule] = []


class InsightQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    story: str
    level: Level


def load_insights(path: Path) -> tuple[dict[str, Story], list[InsightQuestion]]:
    data = yaml.safe_load(path.read_text())
    stories = {s["id"]: Story(**s) for s in data["stories"]}
    return stories, [InsightQuestion(**q) for q in data["questions"]]


def _value(facts: dict[str, Fact], fid: str | None) -> float | None:
    fact = facts.get(fid) if fid else None
    ok = fact is not None and fact.state == "ok" and isinstance(fact.value, int | float)
    return float(fact.value) if ok else None


def direction(claim: Claim, facts: dict[str, Fact]) -> str | None:
    """"up", "down", or ``None``, from the facts that the claim reads."""
    p = claim.params
    if claim.kind == "change":
        d = _value(facts, p.get("pct"))
        return None if d is None or abs(d) <= FLAT else ("up" if d > 0 else "down")
    if claim.kind == "trend":
        values = [_value(facts, i) for i in p.get("points", [])]
        if any(v is None for v in values):
            return None
        ups, downs, _ = _steps(values)
        return "up" if ups > downs else "down" if downs > ups else None
    if claim.kind == "extreme":
        return "up" if p.get("which") == "max" else "down"
    return None


def _misses(claim: Claim, facts: dict[str, Fact]) -> bool | None:
    v, t = _value(facts, claim.params.get("fact")), _value(facts, claim.params.get("target"))
    if v is None or t is None:
        return None
    return v > t if claim.params.get("better") == "lower" else v < t


def _metric(claim: Claim, facts: dict[str, Fact]) -> str | None:
    return next((facts[f].metric for f in claim.facts if f in facts and facts[f].metric), None)


def matches(rule: StoryRule, claim: Claim, facts: dict[str, Fact]) -> bool:
    if claim.kind not in rule.kinds or _metric(claim, facts) != rule.metric:
        return False
    if any(claim.segment.get(k) != v for k, v in rule.segment.items()):
        return False
    if rule.direction is not None and direction(claim, facts) != rule.direction:
        return False
    if rule.misses is not None and _misses(claim, facts) is not rule.misses:
        return False
    return rule.compare_a is None or claim.params.get("a_label") == rule.compare_a


def surfaced(story: Story, claims: list[Claim], facts: dict[str, Fact]) -> bool:
    return story.expressible and any(matches(r, c, facts) for r in story.any_of for c in claims)


# --- the run ------------------------------------------------------------------------------


def report_claims(report) -> tuple[list[Claim], list[Claim]]:
    """The claims that a report cites, and all of its candidate claims."""
    cited = [cid for s in report.spec.sections for cid in cited_claims(s)]
    claims = report.spec.claims
    return [claims[c] for c in dict.fromkeys(cited) if c in claims], list(claims.values())


def score(story: Story, report) -> dict[str, bool]:
    """``cited``: the text states the story. ``listed``: code listed a claim that states it."""
    if report is None:
        return {"cited": False, "listed": False}
    cited, listed = report_claims(report)
    facts = report.facts
    return {"cited": surfaced(story, cited, facts), "listed": surfaced(story, listed, facts)}


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Recall by level, over the questions whose story is expressible."""
    out: dict[str, Any] = {}
    for level in ("direct", "topic", "open", "all"):
        mine = [r for r in rows if r["expressible"] and level in (r["level"], "all")]
        n = len(mine)
        out[level] = {
            "questions": n,
            "cited": sum(r["cited"] for r in mine),
            "listed": sum(r["listed"] for r in mine),
            "cited_recall": sum(r["cited"] for r in mine) / n if n else None,
            "listed_recall": sum(r["listed"] for r in mine) / n if n else None,
        }
    return out


def run(arm: str, runs: int, demo_file: Path, limit: int | None = None,
        follow: str | None = None, answer_clarify: bool = False) -> dict[str, Any]:  # pragma: no cover - needs data
    """``arm`` is ``code`` (the code-only text, no writer call) or ``writer``.

    ``follow`` adds one follow-up section, as a click on a chip: ``ranked`` takes the
    section's top-ranked chip, ``first`` takes the first candidate in vocabulary order (the
    fixed rule before the ranking). The score reads every section of the report.

    ``answer_clarify`` answers a clarify question with its first option, as a person who
    picks the first choice. The row keeps ``clarified`` so the result can show both scores."""
    from living_reports.demos import get_demo
    from living_reports.harness import builder, decide
    from living_reports.harness.builder import AskContext, answer
    from living_reports.reports.store import ReportStore
    from living_reports.semantic.demo import DEMO_SOURCE
    from living_reports.sources.build import build_demo_source
    from living_reports.store import get_store

    stories, questions = load_insights(demo_file)
    questions = questions[:limit]
    store = get_store()
    get_demo().seed(store.graph)
    build_demo_source(2)  # every row of the demo data, as in the routing eval
    for thread in decide.warm():  # a cold load takes longer than one decision may wait
        thread.join()
    reports = ReportStore(store)
    rows: list[dict[str, Any]] = []
    usd: float | None = 0.0
    # The code arm keeps routing and claims and replaces only the writer: every section
    # gets the code-only text, as when every draft fails.
    patch = mock.patch.object(builder, "_narrate", return_value=None) if arm == "code" else None
    with patch or _nothing():
        for n in range(runs):
            for q in questions:
                ctx = AskContext(store=store, reports=reports, source_id=DEMO_SOURCE)
                result = answer(q.question, None, ctx)
                clarified = False
                if answer_clarify and result.kind == "clarify" and result.clarify.options:
                    option = result.clarify.options[0]
                    usd = None if usd is None or result.cost.usd is None else usd + result.cost.usd
                    ctx = AskContext(store=store, reports=reports, source_id=DEMO_SOURCE)
                    result = answer(q.question, None, ctx,
                                    choice=option.query.model_dump(mode="json", exclude_none=True))
                    clarified = True
                report = reports.get(result.report_id) if result.report_id else None
                chip = _chip(report, follow, store) if follow and report else None
                if chip is not None:
                    turn = AskContext(store=store, reports=reports, source_id=DEMO_SOURCE)
                    more = answer(chip.label, report.id, turn, choice=chip.query)
                    report = reports.get(more.report_id) if more.report_id else report
                    usd = None if usd is None or more.cost.usd is None else usd + more.cost.usd
                story = stories[q.story]
                rows.append({"run": n, "question": q.question, "story": q.story, "level": q.level,
                             "expressible": story.expressible, "kind": result.kind,
                             "clarified": clarified,
                             "sections": [s.narration for s in report.spec.sections] if report else [],
                             **score(story, report)})
                usd = None if usd is None or result.cost.usd is None else usd + result.cost.usd
                print(f"{len(rows)}: {q.story} {q.level} {rows[-1]['cited']}", flush=True)
    return {"arm": arm, "follow": follow, "answer_clarify": answer_clarify, "runs": runs, "usd": usd, "summary": summary(rows), "rows": rows}


def _chip(report, follow: str, store):  # pragma: no cover - needs data
    from living_reports.engine import Query
    from living_reports.harness.followups import candidates

    section = report.spec.sections[0]
    if follow == "ranked":
        return section.follow_ups[0] if section.follow_ups else None
    query = Query.model_validate(report.query_defs["q1"])
    leader = next((c for c in report.spec.claims.values() if ".extreme.max." in c.id), None)
    top = None
    if leader is not None and len(query.dimensions) == 1 and query.dimensions[0] in leader.segment:
        top = (leader.segment[query.dimensions[0]], query.dimensions[0])
    found = candidates(query, store.graph.model(), top)
    return found[0] if found else None


class _nothing:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def main() -> None:  # pragma: no cover
    from living_reports.demos import get_demo

    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=["code", "writer"], required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Only the first N questions, for a smoke run.")
    parser.add_argument("--follow", choices=["first", "ranked"], help="Add the section of one chip.")
    parser.add_argument("--answer-clarify", action="store_true",
                        help="Answer a clarify question with its first option.")
    args = parser.parse_args()
    demo_file = Path(__file__).parent / f"insights_{get_demo().name}.yaml"
    args.out.write_text(json.dumps(run(args.arm, args.runs, demo_file, args.limit, args.follow,
                                     args.answer_clarify), indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
