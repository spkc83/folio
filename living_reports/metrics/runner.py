"""Run compiled queries and turn results into traced facts."""
import json
import math
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from living_reports.engine import Query
from living_reports.engine.compile import Compiled, compile
from living_reports.semantic.kinds import MetricBindingPayload
from living_reports.semantic.labels import display_value
from living_reports.semantic.model import SemanticModel
from living_reports.sources import columns_of, data_time_of, normalize, rows_of, truncated
from living_reports.sources.registry import source_for
from living_reports.store import Store

NO_DATA = "No data"
# Rows written before sources existed name a snapshot of the demo data.
LEGACY_SOURCE = "duckdb_demo"


def aggregate_of(metric) -> str:
    """How a metric combines rows: its measure's aggregate, ``ratio``, or ``expression``."""
    if metric.measure is not None:
        return metric.measure.agg
    return "ratio" if metric.ratio is not None else "expression"


class Binding(BaseModel):
    """Where a fact sits in its query: the ref, the metric, and the member keys."""

    ref: str
    metric: str
    keys: dict[str, str | None] = {}
    share: bool = False


class _Sourced(BaseModel):
    """A record of data read from one source. A record saved before sources existed has
    ``snapshot`` in place of ``source_id``: it reads as the demo source."""

    source_id: str

    @model_validator(mode="before")
    @classmethod
    def _legacy_snapshot(cls, data: Any) -> Any:
        if isinstance(data, dict) and "snapshot" in data:
            data = {k: v for k, v in data.items() if k != "snapshot"}
            data.setdefault("source_id", LEGACY_SOURCE)
        return data


class Fact(_Sourced):
    id: str
    label: str
    value: float | str | None
    formatted: str
    query_hash: str
    metric: str | None = None
    metric_version: int | None = None
    # The source's data time when the value was read. None on records from before sources.
    data_time: datetime | None = None
    state: Literal["ok", "empty", "undefined"] = "ok"
    definition: str | None = None
    binding: Binding | None = None
    # How code derived the value from query results, for a derived fact.
    basis: str | None = None


class Result(_Sourced):
    columns: list[str]
    rows: list[list[Any]]
    query_hash: str
    data_time: datetime | None = None
    row_count: int
    sql: str
    params: list[Any]
    metric_versions: dict[str, int]
    metric_meta: dict[str, dict[str, Any]]
    dimensions: list[str]
    graph_version: int = 0
    labels: dict[str, str] = {}
    from_cache: bool = False
    truncated: bool = False


def labels_of(compiled: Compiled, model: SemanticModel) -> dict[str, str]:
    """A label for each result column: member id, time column, or metric id."""
    labels = {}
    for column in compiled.plan.group_columns:
        member, _, grain = column.partition(":")
        labels[column] = grain.capitalize() if grain else model.field(member).label
    return labels


def run_query(
    q: Query,
    source_id: str,
    store: Store,
    valid_at: datetime | None = None,
    known_at: datetime | None = None,
    overrides: dict[str, MetricBindingPayload] | None = None,
) -> Result:
    """Compile ``q`` on the model as of ``valid_at``/``known_at`` and run it on the source.

    ``overrides`` previews proposed definitions. It changes nothing in the graph. The cache
    key holds the source's data time, so new data never gets an old result. A
    ``SourceError`` goes to the caller.
    """
    source = source_for(source_id)
    model = store.graph.model(valid_at, known_at)
    for metric_id, payload in (overrides or {}).items():
        model = model.with_metric(metric_id, payload)
    compiled = compile(q, model, source.dialect)
    data_time = source.data_time()
    hit = store.row(
        "SELECT payload FROM query_cache WHERE query_hash = ? AND source_id = ? "
        "AND data_time = ?",
        [compiled.hash, source_id, data_time.isoformat()],
    )
    if hit:
        return Result(**json.loads(hit[0]), from_cache=True)

    meta = {}
    labels = labels_of(compiled, model)
    for metric_id in q.metrics:
        metric = model.metric(metric_id)
        meta[metric_id] = {
            "label": metric.label,
            "format": metric.format,
            "definition": metric.definition,
            "agg": aggregate_of(metric),
            "better": metric.better,
            "target": metric.target,
        }
        labels[metric_id] = metric.label

    table = normalize(source.run(compiled.sql, compiled.params))
    # The data time of the rows read. A rebuild after the cache lookup changes it.
    data_time = data_time_of(table) or data_time
    rows = rows_of(table)
    result = Result(
        columns=columns_of(table),
        rows=rows,
        query_hash=compiled.hash,
        source_id=source_id,
        data_time=data_time,
        row_count=len(rows),
        sql=compiled.sql,
        params=[str(p) for p in compiled.params],
        metric_versions=compiled.plan.metric_versions,
        metric_meta=meta,
        dimensions=compiled.plan.group_columns,
        graph_version=compiled.plan.model_version,
        labels=labels,
        truncated=truncated(table),
    )
    store.execute(
        "INSERT OR REPLACE INTO query_cache VALUES (?, ?, ?, ?)",
        [
            compiled.hash,
            source_id,
            data_time.isoformat(),
            result.model_dump_json(exclude={"from_cache"}),
        ],
    )
    return result


def format_value(value: Any, fmt: str) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return NO_DATA
    if fmt == "percent":
        return f"{value * 100:.2f}%"
    if fmt == "count":
        return f"{int(value):,}"
    if fmt == "currency":
        size = abs(value)
        sign = "-" if value < 0 else ""
        if size >= 1_000_000:
            return f"{sign}${size / 1_000_000:.1f}M"
        if size >= 1_000:
            return f"{sign}${size / 1_000:.1f}K"
        return f"{sign}${size:.2f}"
    return str(value)


def _keys(dims: list[str], values: list[Any]) -> dict[str, str | None]:
    """Member keys of a fact. A time column (``field:month``) is the cube's ``month`` column."""
    return {
        ("month" if d.endswith(":month") else d): None if v is None else str(v)
        for d, v in zip(dims, values)
    }


def _shown(dims: list[str], values: list[Any]) -> list[str]:
    """The keys of a fact as people read them: ``Identity theft``, ``Jan 2024``."""
    return [display_value(d, v) for d, v in zip(dims, values)]


def ref_of(fact_id: str) -> str:
    """The query ref that a fact comes from: ``q1_total.fraud_loss`` -> ``q1``."""
    return fact_id.split(".", 1)[0].removesuffix("_total")


def _slug(value: Any) -> str:
    return re.sub(r"[^\w.-]", "_", str(value)) if value is not None else "none"


def to_facts(result: Result, prefix: str) -> list[Fact]:
    """One fact for each metric in each row."""
    dims = result.dimensions
    frame = [dict(zip(result.columns, row)) for row in result.rows]
    facts: list[Fact] = []
    for row in frame:
        keys = [row[d] for d in dims]
        for metric, meta in result.metric_meta.items():
            value = row[metric]
            formatted = format_value(value, meta["format"])
            state = "ok" if formatted != NO_DATA else "empty"
            facts.append(
                Fact(
                    id=".".join([prefix, metric, *(_slug(k) for k in keys)]),
                    label=" · ".join([meta["label"], *_shown(dims, keys)]),
                    value=value if state == "ok" else None,
                    formatted=formatted,
                    query_hash=result.query_hash,
                    metric=metric,
                    metric_version=result.metric_versions[metric],
                    source_id=result.source_id,
                    data_time=result.data_time,
                    state=state,
                    definition=meta.get("definition") or None,
                    binding=Binding(ref=ref_of(prefix), metric=metric, keys=_keys(dims, keys)),
                )
            )
    return facts


def share_facts(
    result: Result, metric: str, prefix: str, whole: Result | None = None
) -> list[Fact]:
    """Each row's share of the metric total.

    Pass ``whole`` when the rows can be a subset, as with ``top``. The sum of the rows is
    then the wrong denominator.
    """
    meta = result.metric_meta[metric]
    index = result.columns.index(metric)
    if whole is not None and whole.rows:
        total = whole.rows[0][whole.columns.index(metric)] or 0
    else:
        total = sum(row[index] or 0 for row in result.rows)
    facts = []
    for row in result.rows:
        keys = [row[result.columns.index(d)] for d in result.dimensions]
        share = (row[index] or 0) / total if total else None
        formatted = format_value(share, "percent")
        facts.append(
            Fact(
                id=".".join([prefix, metric, "share", *(_slug(k) for k in keys)]),
                label=" · ".join([f"Share of {meta['label'].lower()}", *_shown(result.dimensions, keys)]),
                value=share,
                formatted=formatted,
                query_hash=result.query_hash,
                metric=metric,
                metric_version=result.metric_versions[metric],
                source_id=result.source_id,
                data_time=result.data_time,
                state="ok" if share is not None else "empty",
                binding=Binding(
                    ref=ref_of(prefix), metric=metric,
                    keys=_keys(result.dimensions, keys), share=True,
                ),
            )
        )
    return facts
