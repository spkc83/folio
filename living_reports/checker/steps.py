"""Deterministic checks. Each one compares the edition with a fresh run on the source."""
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from living_reports.engine.query import is_time_column
from living_reports.harness.claims import expected_derived, render_claims
from living_reports.metrics.runner import Fact, Result, share_facts, to_facts
from living_reports.reports.editions import Edition, coverage_of
from living_reports.semantic.graph import FolioGraph
from living_reports.sources import normalize, rows_of
from living_reports.sources.registry import source_for
from living_reports.spec.models import Action, Prose
from living_reports.spec.validate import validate_spec

TOLERANCE = 1e-9


class Finding(BaseModel):
    where: str
    detail: str


class CheckResult(BaseModel):
    step: Literal["trace", "totals", "rules", "wording"]
    status: Literal["pass", "fail", "open"]
    findings: list[Finding] = []


def _result(step: str, findings: list[Finding]) -> CheckResult:
    return CheckResult(step=step, status="fail" if findings else "pass", findings=findings)


def _fresh(result: Result) -> Result:
    rows = rows_of(normalize(source_for(result.source_id).run(result.sql, result.params)))
    return result.model_copy(update={"rows": rows})


def _expected(ref: str, result: Result, whole: Result | None) -> dict[str, Fact]:
    facts = to_facts(result, ref)
    for metric, meta in result.metric_meta.items():
        timed = any(is_time_column(d) for d in result.dimensions)
        if result.dimensions and meta["format"] != "percent" and not timed:
            facts += share_facts(result, metric, ref, whole)
    return {f.id: f for f in facts}


def _same_rows(a: list, b: list) -> bool:
    if len(a) != len(b):
        return False
    return not any(
        len(x) != len(y) or any(_differs(v, w) for v, w in zip(x, y)) for x, y in zip(a, b)
    )


def check_trace(edition: Edition) -> CheckResult:
    """Each fact must equal a fresh result of its own query."""
    fresh = {ref: _fresh(result) for ref, result in edition.queries.items()}
    expected: dict[str, Fact] = {}
    findings = []
    for ref, result in fresh.items():
        expected |= _expected(ref, result, fresh.get(f"{ref}_total"))
        if not _same_rows(result.rows, edition.queries[ref].rows):
            findings.append(Finding(where=ref, detail="Stored rows differ from a fresh run."))
    for issue in validate_spec(edition.spec, edition.facts, edition.queries):
        findings.append(Finding(where=issue.path, detail=f"{issue.code}: {issue.message}"))
    # A derived fact (a half-year total, a ratio) must equal code's derivation from fresh rows.
    expected |= expected_derived(edition.spec, edition.query_defs, fresh, expected)
    for fact_id, fact in edition.facts.items():
        truth = expected.get(fact_id)
        if truth is None:
            findings.append(Finding(where=fact_id, detail="No query produces this value."))
        elif fact.query_hash != truth.query_hash:
            findings.append(Finding(where=fact_id, detail="The value cites the wrong query."))
        elif fact.formatted != truth.formatted or _differs(fact.value, truth.value):
            findings.append(
                Finding(where=fact_id, detail=f"Shown {fact.formatted}, query gives {truth.formatted}.")
            )
    return _result("trace", findings)


def _differs(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(a - b) > TOLERANCE * max(1.0, abs(b))
    return a != b


def check_totals(edition: Edition, graph: FolioGraph) -> CheckResult:
    """Breakdowns must sum to their total, and the data must match the frozen coverage."""
    findings = []
    for ref, result in edition.queries.items():
        total = edition.queries.get(f"{ref}_total")
        definition = edition.query_defs.get(ref, {})
        if total is None or definition.get("limit") or definition.get("top"):
            continue
        for metric, meta in result.metric_meta.items():
            if meta["format"] == "percent":
                continue
            index = result.columns.index(metric)
            parts = sum(row[index] or 0 for row in result.rows)
            whole = total.rows[0][total.columns.index(metric)] or 0
            if abs(parts - whole) > 1e-6 * max(1.0, abs(whole)):
                findings.append(
                    Finding(where=ref, detail=f"{metric}: rows sum to {parts}, total is {whole}.")
                )
    model = graph.model(edition.known_at, edition.known_at)
    current = coverage_of(edition.source_id, model)
    if current != edition.coverage:
        findings.append(Finding(where="coverage", detail=f"Expected {edition.coverage}."))
    return _result("totals", findings)


def report_texts(edition: Edition) -> list[tuple[str, str]]:
    items = [("title", edition.spec.title)]
    items += [(f"assumption.{a.id}", a.text) for a in edition.spec.assumptions]
    for section in edition.spec.sections:
        items.append((f"{section.id}.heading", section.heading))
        for block in section.blocks:
            if isinstance(block, Prose | Action):
                items.append((section.id, render_claims(block.text, edition.spec.claims)))
    return items


def check_rules(edition: Edition, graph: FolioGraph, rules_path: str) -> CheckResult:
    """Compliance rules: banned claims and the metric versions in force at the freeze."""
    with open(rules_path) as handle:
        rules = yaml.safe_load(handle)
    findings = []
    for where, text in report_texts(edition):
        lowered = text.lower()
        for phrase in rules["banned_phrases"]:
            if phrase in lowered:
                findings.append(Finding(where=where, detail=f'Banned phrase: "{phrase}".'))
    frozen = graph.slice(known_at=edition.known_at)
    for fact_id, fact in edition.facts.items():
        if fact.metric is None:
            continue
        node = frozen.node(fact.metric)
        if node is None or node.kind != "MetricBinding" or node.version != fact.metric_version:
            findings.append(Finding(where=fact_id, detail="This is not a certified metric version."))
    return _result("rules", findings)
