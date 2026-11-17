"""Deterministic exploration: the report's cube, and the scoped query for drill and pin.

This module is the narrow interface between the report page and the query engine. The
models ``CubeView``, ``MetricAgg``, and ``ScopedQuery`` are the contract with the web app.

The cube carries each metric as additive components at the grain of the report's
dimensions plus month. A sum or a count re-aggregates as a sum. An average, or a ratio of two
sums or counts, re-aggregates as ``sum(num) / sum(den)``. Any other metric (count distinct,
min, max, an expression) is carried at the cube grain but is not re-aggregable, and the page
says so.

The cube covers only what the report covers: the rows of its widest query, that query's own
dates plus exactly one previous period, and for a top query only the members it shows.
"""
import hashlib
import json
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlglot import exp

from living_reports.engine import Filter, FilterGroup, OutOfScope, Query, QueryRejected, TimeRange
from living_reports.engine.compile import compile
from living_reports.engine.query import flat_filters, stored_query
from living_reports.semantic.kinds import Measure, MetricBindingPayload
from living_reports.semantic.model import Metric, SemanticModel
from living_reports.sources import data_time_of, normalize, rows_of, truncated
from living_reports.sources.registry import source_for
from living_reports.store import Store

CUBE_MAX_ROWS = 5000
MONTH = "month"
TOO_LARGE = "The report has more than 5,000 cells."
MID_MONTH = "A query has a date bound that is not on day 1 of a month, so the report has no monthly cells."
DIFFERENT_FILTERS = (
    "The report's queries cover different rows, and no one query covers the others, "
    "so the report has no cells to explore."
)
TOP_WITH_OTHERS = (
    "A query shows only its top members, and other queries show more, "
    "so the report has no cells to explore."
)
TOP_SHAPE = "A query shows only its top rows by more than one dimension, so the report has no cells to explore."
NOT_MONTHLY = "A query has a time grain other than month, so the report has no monthly cells."
NO_MEMBERS = "The query shows no members, so the report has no cells to explore."
OUTSIDE = "The range is outside the report's dates."
HAVING = "A query keeps only rows whose metric values pass a test, so the report has no cells to explore."


class MetricAgg(BaseModel):
    """How one metric re-aggregates. ``num`` and ``den`` are column names in the cube."""

    kind: Literal["sum", "count", "ratio", "other"]
    num: str
    den: str | None = None
    format: str
    label: str


class CubeView(BaseModel):
    """``dimensions`` are member ids and end with ``"month"``, the cube's time column."""

    dimensions: list[str]
    labels: dict[str, str]
    vocabulary: dict[str, str]
    metrics: dict[str, MetricAgg]
    columns: list[str]
    rows: list[list[Any]]
    span: tuple[str | None, str | None]
    # The cube's identity: the compiled query, the source, and the source's data time.
    query_hash: str
    source_id: str | None = None
    data_time: datetime | None = None


class ScopedQuery(BaseModel):
    """A report query under the reader's scope. ``range`` is ``[first, last]`` month, inclusive.

    ``by`` and the keys of ``member`` are member ids.
    """

    ref: str
    filters: list[Filter] = []
    range: tuple[str, str] | None = None
    by: str | None = None
    member: dict[str, str | None] = {}


def _shift(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def _bounds(q: Query) -> tuple[date | None, date | None]:
    return (q.time.start, q.time.end) if q.time else (None, None)


def _filter_key(f: Filter | FilterGroup) -> str:
    return json.dumps(f.model_dump(mode="json", by_alias=True), sort_keys=True)


def _contains(outer: Query, inner: Query) -> bool:
    """True when every row that ``inner`` reads is a row that ``outer`` reads."""
    if not {_filter_key(f) for f in outer.filters} <= {_filter_key(f) for f in inner.filters}:
        return False
    (os, oe), (is_, ie) = _bounds(outer), _bounds(inner)
    starts = os is None or (is_ is not None and is_ >= os)
    ends = oe is None or (ie is not None and ie <= oe)
    return starts and ends


def _widest(queries: list[Query]) -> Query | None:
    """The query whose rows contain the rows of every other query, if one exists.

    The cube reads exactly these rows. It never covers rows or dates that no report query
    covers, so an exported file cannot widen what the report shows.
    """
    return next((q for q in queries if all(_contains(q, other) for other in queries)), None)


def _additive(metric: Metric) -> bool:
    m = metric.measure
    return m is not None and m.agg in ("sum", "count")


def _components(
    metric: Metric, model: SemanticModel
) -> tuple[MetricAgg, dict[str, MetricBindingPayload]]:
    """The metric as additive components, read from its typed definition.

    Returns how it re-aggregates and the component metrics to add to the model for the cube.
    """
    agg = {"format": metric.format, "label": metric.label}
    m = metric.measure
    if m is not None and m.agg in ("sum", "count"):
        return MetricAgg(kind=m.agg, num=metric.id, **agg), {}
    if m is not None and m.agg == "avg":
        num, den = f"{metric.id}__num", f"{metric.id}__den"

        def part(a: str) -> MetricBindingPayload:
            return MetricBindingPayload(
                name=f"{metric.id}__{a}", label=metric.label, format="number",
                measure=Measure(agg="sum" if a == "num" else "count", field=m.field, filter=m.filter),
            )

        return MetricAgg(kind="ratio", num=num, den=den, **agg), {num: part("num"), den: part("den")}
    if metric.ratio is not None:
        top, bottom = model.metric(metric.ratio.numerator), model.metric(metric.ratio.denominator)
        if _additive(top) and _additive(bottom):
            return MetricAgg(kind="ratio", num=top.id, den=bottom.id, **agg), {}
    return MetricAgg(kind="other", num=metric.id, **agg), {}


def build_cube(defs: dict[str, dict[str, Any]], source_id: str, store: Store) -> CubeView | None:
    """One pre-aggregated table for the report, or ``None`` when the report cannot have one."""
    return cube_or_reason(defs, source_id, store)[0]


def cube_or_reason(
    defs: dict[str, dict[str, Any]], source_id: str, store: Store
) -> tuple[CubeView | None, str | None]:
    """The cube, or ``None`` with the reason that the page shows.

    A ``SourceError`` goes to the caller.
    """
    if not defs:
        return None, None
    model = store.graph.model()
    queries = [stored_query(d, model) for d in defs.values()]
    if any(b is not None and b.day != 1 for q in queries for b in _bounds(q)):
        return None, MID_MONTH
    if any(q.time and q.time.grain not in (None, MONTH) for q in queries):
        return None, NOT_MONTHLY
    if any(q.having for q in queries):
        return None, HAVING
    if any(q.limit for q in queries) and len(queries) > 1:
        return None, TOP_WITH_OTHERS
    widest = _widest(queries)
    if widest is None:
        return None, DIFFERENT_FILTERS
    if widest.limit and (len(widest.dimensions) != 1 or widest.is_time_series):
        return None, TOP_SHAPE

    named = [
        d for q in queries
        for d in [*q.dimensions, *(f.member for f in flat_filters(q.filters))]
    ]
    dimensions = list(dict.fromkeys(named))
    metrics: dict[str, MetricAgg] = {}
    extra: dict[str, MetricBindingPayload] = {}
    for metric_id in dict.fromkeys(m for q in queries for m in q.metrics):
        agg, parts = _components(model.metric(metric_id), model)
        metrics[metric_id] = agg
        extra |= parts
    for metric_id, payload in extra.items():
        model = model.with_metric(metric_id, payload)
    columns_wanted = list(dict.fromkeys(c for a in metrics.values() for c in (a.num, a.den) if c))

    # The query's own dates and exactly one previous period of the same length.
    start, end = _bounds(widest)
    first = start
    if start is not None and end is not None:
        first = _shift(start, -_months_between(start, end))
    field = widest.time.field if widest.time else None
    filters = list(widest.filters)
    if widest.limit:
        shown = _shown_members(widest, source_id, store)
        values = [v for v in shown if v is not None]
        member = widest.dimensions[0]
        parts: list = [Filter(member=member, op="in", values=values)] if values else []
        if None in shown:
            parts.append(Filter(member=member, op="is_null"))
        if not parts:
            return None, NO_MEMBERS
        filters.append(FilterGroup(any=parts) if len(parts) > 1 else parts[0])
    cube_query = Query(
        metrics=columns_wanted, dimensions=dimensions, filters=filters,
        time=TimeRange(field=field, start=first, end=end, grain=MONTH),
    )
    source = source_for(source_id)
    try:
        compiled = compile(cube_query, model, source.dialect)
    except (OutOfScope, QueryRejected) as error:
        return None, error.reason
    # The outer select names the columns in cube order: members, month (an ISO date), then
    # the components as doubles. It orders by members and month and stops past the cap.
    # Every name in it is a member id, a metric id, or the compiled time column. sqlglot
    # renders it in the source's dialect, with the compiled placeholders in place.
    select = [exp.column(d, quoted=True) for d in dimensions]
    month = exp.cast(exp.cast(exp.column(compiled.plan.time_column, quoted=True), "DATE"), "VARCHAR")
    select.append(exp.alias_(month, MONTH, quoted=True))
    select += [exp.alias_(exp.cast(exp.column(c, quoted=True), "DOUBLE"), c, quoted=True)
               for c in columns_wanted]
    keys = [exp.Literal.number(i + 1) for i in range(len(dimensions) + 1)]
    sql = (
        exp.select(*select)
        .from_(exp.Subquery(this=compiled.ast.copy(), alias=exp.TableAlias(this=exp.to_identifier("cube"))))
        .order_by(*keys)
        .limit(CUBE_MAX_ROWS + 1)
        .sql(dialect=source.dialect)
    )
    table = source.run(sql, compiled.params, limit=CUBE_MAX_ROWS)
    if truncated(table):
        return None, TOO_LARGE
    data_time = data_time_of(table) or source.data_time()
    rows = rows_of(normalize(table))
    columns = [*dimensions, MONTH, *columns_wanted]
    digest = json.dumps([compiled.hash, source_id, data_time.isoformat()], sort_keys=True)
    members = model.members
    vocabulary = {i: f.label for i, f in members.items() if f.dimension is not None and not f.dimension.is_time}
    labels = {d: members[d].label for d in dimensions} | {MONTH: "Month"}
    cube = CubeView(
        dimensions=[*dimensions, MONTH],
        labels=labels,
        vocabulary=vocabulary,
        metrics=metrics,
        columns=columns,
        rows=rows,
        span=(first.isoformat() if first else None, end.isoformat() if end else None),
        query_hash=hashlib.sha256(digest.encode()).hexdigest(),
        source_id=source_id,
        data_time=data_time,
    )
    return cube, None


def _shown_members(query: Query, source_id: str, store: Store) -> list[Any]:
    """The members that a top query shows, run on the engine as the report ran it."""
    from living_reports.metrics.runner import run_query

    result = run_query(query, source_id, store)
    index = result.columns.index(query.dimensions[0])
    return [row[index] for row in result.rows]


def scoped_query(defs: dict[str, dict[str, Any]], sq: ScopedQuery, model: SemanticModel) -> Query:
    """The report query with the scope's filters, the member, the range, and a new grouping."""
    query = stored_query(defs[sq.ref], model)
    if any(v is None for v in sq.member.values()):
        raise ValueError("A member with no value cannot be queried")
    members = [Filter(member=d, op="eq", values=[v]) for d, v in sq.member.items()]
    update: dict[str, Any] = {"filters": [*query.filters, *sq.filters, *members]}
    if sq.by:
        update["dimensions"] = [sq.by]
        update["order"] = []
        # A breakdown by one member: no time grain, as the export drill groups by [by] only.
        if query.time is not None and query.time.grain is not None:
            plain = query.time.model_copy(update={"grain": None})
            update["time"] = plain if (plain.start or plain.end) else None
    else:
        # A dimension that the scope fixes to one member has one row: show it as a total.
        fixed = {
            f.member for f in [*sq.filters, *members]
            if f.op == "eq" or (f.op == "in" and len(f.values) == 1)
        }
        update["dimensions"] = [d for d in query.dimensions if d not in fixed]
        update["order"] = [o for o in query.order if o[0] not in fixed]
    if sq.range:
        first, last = (date.fromisoformat(m) for m in sorted(sq.range))
        end = _shift(last, 1)
        start0, end0 = _bounds(query)
        # The scope narrows the query. It never reaches outside the query's own dates.
        start = max(first, start0) if start0 else first
        end = min(end, end0) if end0 else end
        if start >= end:
            raise ValueError(OUTSIDE)
        base = update.get("time", query.time) or TimeRange(start=first, end=_shift(last, 1))
        update["time"] = base.model_copy(update={"start": start, "end": end})
    # Validate again: the new grouping must still satisfy the query rules.
    return Query.model_validate(query.model_copy(update=update).model_dump(by_alias=True))


def _label(key: str, labels: dict[str, str]) -> str:
    return labels.get(key) or key.split(".")[-1].replace("_", " ").capitalize()


def describe(
    defs: dict[str, dict[str, Any]], sq: ScopedQuery, labels: dict[str, str], model: SemanticModel
) -> str:
    """The scoped query in words. A pinned section uses this as its intent."""
    from living_reports.reports.payload import period_label

    query = scoped_query(defs, sq, model)
    head = _label(query.metrics[0], labels)
    by = query.dimensions
    if by:
        head += " by " + " and ".join(_label(d, labels).lower() for d in by)
    parts = [head]
    for f in sq.filters:
        value = ", ".join(str(v) for v in f.values)
        parts.append(f"{_label(f.member, labels)} {'≠' if f.op == 'ne' else '='} {value}")
    parts += [f"{_label(d, labels)} = {v if v is not None else 'No value'}" for d, v in sq.member.items()]
    if sq.range:
        first, last = (date.fromisoformat(m) for m in sorted(sq.range))
        parts.append(period_label(first, _shift(last, 1)))
    return " · ".join(parts)
