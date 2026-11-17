"""Measure cost per correct answer for one model tier.

Run: python -m living_reports.evals.run --tier small --runs 3 --out results.json
"""
import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from living_reports.demos import get_demo
from living_reports.engine import OutOfScope, Query
from living_reports.harness.builder import AskContext, answer
from living_reports.harness.ladder import run_ladder
from living_reports.metrics.runner import run_query
from living_reports.reports.store import ReportStore
from living_reports.semantic.demo import DEMO_SOURCE
from living_reports.sources.build import build_demo_source
from living_reports.store import Store, get_store


class EvalQuestion(BaseModel):
    question: str
    expected_kind: str
    expected_query: dict[str, Any] | None = None


def load_questions() -> list[EvalQuestion]:
    """The eval questions of the demo that ``LR_DEMO`` names."""
    return [EvalQuestion(**q) for q in yaml.safe_load(get_demo().questions.read_text())]


def _rows(query: Query, source_id: str, store: Store) -> list:
    result = run_query(query, source_id, store)
    return sorted((tuple(r) for r in result.rows), key=str)


def score_answer(
    actual: Query, expected: Query, source_id: str, store: Store
) -> bool:
    """Correct means the same result. The SQL text can differ."""
    try:
        return _rows(actual, source_id, store) == _rows(expected, source_id, store)
    except OutOfScope:
        return False


def _is_correct(item: EvalQuestion, result, ctx: AskContext) -> bool:
    if result.kind != item.expected_kind:
        return False
    if item.expected_kind != "report":
        return True
    report = ctx.reports.get(result.report_id)
    actual = Query.model_validate(report.query_defs["q1"])
    expected = Query.model_validate(item.expected_query)
    return score_answer(actual, expected, ctx.source_id, ctx.store)


def evaluate(
    questions: list[EvalQuestion], tier: str, runs: int, ctx: AskContext, ask=answer
) -> dict[str, Any]:
    correct = total = calls = input_tokens = output_tokens = 0
    usd: float | None = 0.0
    misses: list[dict[str, str]] = []
    answers: list[dict[str, Any]] = []
    for run in range(runs):
        for item in questions:
            turn = AskContext(
                store=ctx.store, reports=ctx.reports, source_id=ctx.source_id, tier=tier
            )
            result = ask(item.question, None, turn)
            total += 1
            ok = _is_correct(item, result, turn)
            if ok:
                correct += 1
            else:
                misses.append({"run": str(run), "question": item.question, "got": result.kind})
            answers.append(_narration_of(item, result, turn, run) | {"correct": ok})
            calls += result.cost.calls
            input_tokens += result.cost.input_tokens
            output_tokens += result.cost.output_tokens
            usd = None if usd is None or result.cost.usd is None else usd + result.cost.usd
            print(f"{total}: correct {correct}, cost {usd}", flush=True)
    return {
        "tier": tier,
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": usd,
        "usd_per_correct": usd / correct if usd is not None and correct else None,
        "tokens_per_correct": (input_tokens + output_tokens) / correct if correct else None,
        "misses": misses,
        "narration": narration_summary(answers),
        "routing": routing_summary(answers),
        "answers": answers,
    }


def _narration_of(item: EvalQuestion, result, ctx: AskContext, run: int) -> dict[str, Any]:
    """How the text of one answer was written: per section, "model" or "code"; per draft,
    its outcome."""
    sections = []
    if result.kind == "report" and result.report_id:
        sections = [s.narration for s in ctx.reports.get(result.report_id).spec.sections]
    return {"run": run, "question": item.question, "kind": result.kind,
            "narration": sections, "drafts": list(ctx.drafts), "notes": list(ctx.draft_notes),
            "texts": list(ctx.draft_texts), "routes": list(ctx.routes)}


def routing_summary(answers: list[dict[str, Any]]) -> dict[str, int]:
    """Who routed each answer (its first route), and how many of those were correct."""
    out = {"decision": 0, "model": 0, "decision_correct": 0, "model_correct": 0}
    for a in answers:
        if a["routes"]:
            who = a["routes"][0]
            out[who] += 1
            out[f"{who}_correct"] += int(a["correct"])
    return out


def narration_summary(answers: list[dict[str, Any]]) -> dict[str, Any]:
    """Hypothesis H2: the fallback rate and the drafts per section."""
    sections = sum(len(a["narration"]) for a in answers)
    code = sum(a["narration"].count("code") for a in answers)
    drafts = [d for a in answers for d in a["drafts"]]
    outcomes: dict[str, int] = {}
    for d in drafts:
        outcomes[d] = outcomes.get(d, 0) + 1
    return {
        "sections": sections,
        "model_written": sections - code,
        "code_written": code,
        "fallback_rate": code / sections if sections else None,
        "drafts": len(drafts),
        "drafts_per_section": len(drafts) / sections if sections else None,
        "first_draft_passed": sum(1 for a in answers if a["drafts"][:1] == ["passed"]),
        "outcomes": outcomes,
    }


def _ladder_ask(question: str, report_id: str | None, ctx: AskContext):
    return run_ladder(question, report_id, ctx).answer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default="small")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--source", default=DEMO_SOURCE, help="The source id to ask.")
    parser.add_argument("--seed-source", default=DEMO_SOURCE, help="Seed the demo model on it.")
    parser.add_argument("--seed-dialect", default="duckdb")
    parser.add_argument("--seed-schema", default="main")
    parser.add_argument("--out", required=True)
    parser.add_argument("--ladder", action="store_true", help="Use the escalation ladder.")
    parser.add_argument("--limit", type=int, help="Use only every Nth question, for a sample.")
    args = parser.parse_args()
    store = get_store()
    get_demo().seed(
        store.graph,
        source_id=args.seed_source,
        dialect=args.seed_dialect,
        schema=args.seed_schema,
    )
    if args.source == DEMO_SOURCE:
        build_demo_source(2)  # the evals use every row of the demo data
    ctx = AskContext(store=store, reports=ReportStore(store), source_id=args.source)
    ask = _ladder_ask if args.ladder else answer
    questions = load_questions()[:: args.limit or 1]
    result = evaluate(questions, args.tier, args.runs, ctx, ask=ask)
    Path(args.out).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
