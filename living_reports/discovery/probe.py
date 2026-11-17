"""Probes: each proposed relationship and metric runs once on the real data (spec 7.3).

A relationship's ``to`` side must be unique: one statement counts its rows and its
distinct keys. A metric compiles through the engine (which runs the read-only check) and
runs once with ``limit=1``. Every statement goes through ``check_select`` or ``compile``
and then through the source's read-only ``run`` with a row cap and a timeout.
"""
from typing import Literal

from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError

from folio_graph import NodeChange
from living_reports.engine import OutOfScope, Query, QueryRejected
from living_reports.engine.check import check_select
from living_reports.engine.compile import compile
from living_reports.metrics.runner import format_value
from living_reports.semantic.kinds import RelationshipPayload
from living_reports.semantic.model import SemanticModel
from living_reports.sources import Source, SourceError, rows_of
from living_reports.sources.arrow import normalize

PROBE_TIMEOUT_S = 20


class Probe(BaseModel):
    target: str
    kind: Literal["uniqueness", "metric"]
    status: Literal["pass", "fail"]
    detail: str
    value: str | None = None
    sql: str


def _uniqueness_sql(rel: RelationshipPayload, model: SemanticModel, dialect: str) -> str:
    to = model.dataset(rel.to_dataset)
    cols = [exp.column(c) for c in rel.to_columns]
    key = cols[0] if len(cols) == 1 else exp.Tuple(expressions=cols)
    # COUNT(key) skips NULL keys, which never match a join, so they are not duplicates.
    rows = exp.Count(this=cols[0].copy() if len(cols) == 1 else exp.Star())
    select = exp.select(
        exp.alias_(rows, "n"),
        exp.alias_(exp.Count(this=exp.Distinct(expressions=[key])), "d"),
    ).from_(exp.to_table(to.table))
    return check_select(select, model, dialect).sql(dialect=dialect)


def probe_relationship(rel: RelationshipPayload, model: SemanticModel, source: Source) -> Probe:
    keys = f"{rel.to_dataset}.{','.join(rel.to_columns)}"

    def fail(detail: str, sql: str = "") -> Probe:
        return Probe(target=rel.name, kind="uniqueness", status="fail", detail=detail, sql=sql)

    try:
        sql = _uniqueness_sql(rel, model, source.dialect)
    except QueryRejected as error:
        return fail(error.reason)
    except (SqlglotError, KeyError) as error:
        return fail(f"The probe for {keys} cannot be built: {error}")
    try:
        table = source.run(sql, (), limit=1, timeout_s=PROBE_TIMEOUT_S)
    except SourceError as error:
        return fail(str(error), sql)
    n, d = rows_of(table)[0]
    if n == d:
        return Probe(target=rel.name, kind="uniqueness", status="pass",
                     detail=f"{keys} is unique over {n:,} rows", sql=sql)
    return fail(f"{keys} has {n - d:,} duplicate keys", sql)


def probe_metric(metric_id: str, model: SemanticModel, source: Source) -> Probe:
    sql = ""
    try:
        compiled = compile(Query(metrics=[metric_id]), model, source.dialect)
        sql = compiled.sql
        table = normalize(
            source.run(compiled.sql, compiled.params, limit=1, timeout_s=PROBE_TIMEOUT_S)
        )
    except OutOfScope as error:
        # The reason is the sentence for a reader; the detail names the cause (the join path).
        detail = error.reason if error.detail == error.reason else f"{error.reason} ({error.detail})"
        return Probe(target=metric_id, kind="metric", status="fail", detail=detail, sql=sql)
    except (QueryRejected, SourceError) as error:
        return Probe(target=metric_id, kind="metric", status="fail", detail=str(error), sql=sql)
    rows = rows_of(table)
    metric = model.metric(metric_id)
    value = format_value(rows[0][0] if rows else None, metric.format)
    return Probe(target=metric_id, kind="metric", status="pass",
                 detail=f"{metric.label}: {value}", value=value, sql=sql)


def probe_changes(
    changes: list[NodeChange], model: SemanticModel, source: Source
) -> dict[str, Probe]:
    """A probe per relationship and metric change, keyed by change id.

    Metrics are probed on the model without the relationships that failed, so a metric
    that needs a failed relationship fails with "No join path".
    """
    probes: dict[str, Probe] = {}
    for c in changes:
        if c.kind == "Relationship":
            probes[c.id] = probe_relationship(RelationshipPayload(**c.payload), model, source)
    failed = {k for k, p in probes.items() if p.status == "fail"}
    if failed:
        model = model.model_copy(
            update={"relationships": [r for r in model.relationships if r.name not in failed]}
        )
    for c in changes:
        if c.kind == "MetricBinding":
            probes[c.id] = probe_metric(c.id, model, source)
    return probes
