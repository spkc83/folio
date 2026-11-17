"""The weekly digest: top changes and suggested questions, built with no model call."""
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from living_reports.engine import OutOfScope, Query, QueryRejected
from living_reports.engine.compile import compile
from living_reports.engine.query import upgrade_definition
from living_reports.harness.builder import AskContext
from living_reports.reports.certified import all_certified
from living_reports.reports.changes import ChangeItem, open_changes
from living_reports.semantic.model import Metric, SemanticModel

KIND_RANK = {"definition": 0, "question": 1, "new_data": 2, "new_value": 3, "new_column": 4}


class SuggestedQuestion(BaseModel):
    question: str
    certified_id: str | None = None
    query: dict[str, Any] | None = None


class Digest(BaseModel):
    since: datetime
    until: datetime
    top_changes: list[ChangeItem]
    suggested_questions: list[SuggestedQuestion]


def _metric_for(flt: dict, model: SemanticModel) -> tuple[Metric, dict] | None:
    """The first metric that can answer a question filtered by ``flt``.

    Metrics on the dataset with a time field (the fact table, ``orders``) come first, so
    "Fraud loss for Marketplace" comes before "Dealers for Marketplace".
    """
    timed = next((d.name for d in model.datasets if d.time_field), None)

    def on_fact_table(metric: Metric) -> bool:
        m = metric.measure
        base = m.field.split(".", 1)[0] if m and m.field else m.dataset if m else None
        return base == timed

    for metric in sorted(model.metrics, key=lambda m: not on_fact_table(m)):
        query = upgrade_definition({"metrics": [metric.id], "filters": [flt]}, model)
        try:
            compile(Query.model_validate(query), model)
        except (OutOfScope, QueryRejected, ValidationError):
            continue
        return metric, query
    return None


def _suggestions(items: list[ChangeItem], ctx: AskContext) -> list[SuggestedQuestion]:
    certified = {cq.id: cq for cq in all_certified(ctx.store)}
    out = [
        SuggestedQuestion(question=certified[cid].question, certified_id=cid)
        for i in items
        if i.kind == "question" and (cid := i.subject.get("certified_id")) in certified
    ]
    model = ctx.store.graph.model()
    for i in items:
        if i.kind != "new_value" or not {"dimension", "value"} <= i.subject.keys():
            continue
        dimension, value = i.subject["dimension"], i.subject["value"]
        # Items saved before the query engine name a bare dimension (``region``).
        flt = (
            {"member": dimension, "op": "eq", "values": [value]}
            if "." in dimension
            else {"dimension": dimension, "op": "eq", "value": value}
        )
        found = _metric_for(flt, model)
        if found is not None:
            metric, query = found
            out.append(SuggestedQuestion(question=f"{metric.label} for {value}", query=query))
    out += [SuggestedQuestion(question=cq.question, certified_id=cq.id) for cq in certified.values()]
    unique: dict[str, SuggestedQuestion] = {}
    for suggestion in out:
        unique.setdefault(suggestion.question, suggestion)
    return list(unique.values())


def build_digest(ctx: AskContext, days: int = 7, top: int = 5, questions: int = 4) -> Digest:
    until = ctx.store.now()
    since = until - timedelta(days=days)
    items = [
        i for i in open_changes(ctx, within_days=days)
        if i.detected_at is not None and i.detected_at >= since
    ]
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (KIND_RANK.get(pair[1].kind, 9), -len(pair[1].affected_reports), pair[0]),
    )
    return Digest(
        since=since,
        until=until,
        top_changes=[i for _, i in ranked][:top],
        suggested_questions=_suggestions(items, ctx)[:questions],
    )
