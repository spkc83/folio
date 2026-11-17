"""The payload that the one renderer reads, for drafts and for editions."""
import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from living_reports.metrics.runner import Fact, Result, to_facts
from living_reports.reports.explore import CubeView
from living_reports.spec.models import ReportSpec

Tier = Literal["exploratory", "team", "business_critical"]


class Point(BaseModel):
    label: str
    value: float | str | None
    formatted: str
    fact_id: str


class QueryView(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    query_hash: str
    points: list[Point]
    series: str | None = None
    # Column id (member id, time column, or metric id) to the label people read.
    labels: dict[str, str] = {}


class Verified(BaseModel):
    """Who certified the report's question, and when. ``current`` is False after a
    definition that the question uses changed."""

    by: str
    at: datetime
    current: bool


class ReportPayload(BaseModel):
    id: str
    spec: ReportSpec
    facts: dict[str, Fact]
    queries: dict[str, QueryView]
    edition: int | None = None
    state: Literal["draft", "published"] = "draft"
    tier: Tier = "exploratory"
    view: str
    cost: dict[str, Any] = {}
    verified: Verified | None = None
    cube: CubeView | None = None
    cube_reason: str | None = None
    query_defs: dict[str, dict[str, Any]] = {}
    # The draft's author and contributors, so the page can show who may change its words.
    author: str = ""
    contributors: list[str] = []


Period = tuple[date, date]
_MONTH_NAMES = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _shift(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _month_span(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def periods(
    start: date | None, end: date | None, months: list[str]
) -> tuple[Period, Period] | None:
    """The current period and the previous period of the same length. Ends are exclusive.

    A bounded query compares ``[start, end)`` with the same number of months before it. An
    unbounded query compares its last twelve months with the twelve before. Any other case
    has no previous period that the month grain can state exactly.
    """
    if start is not None and end is not None:
        if start.day != 1 or end.day != 1 or end <= start:
            return None
        return (start, end), (_shift(start, -_month_span(start, end)), start)
    if start is not None or end is not None or len(months) < 24:
        return None
    last = date.fromisoformat(max(months))
    first = _shift(last, -11)
    return (first, _shift(last, 1)), (_shift(first, -12), first)


def period_label(start: date, end: date) -> str:
    """``2025`` for a calendar year, else ``Jul 2025 – Sep 2025``. ``end`` is exclusive."""
    if start.month == 1 and end == date(start.year + 1, 1, 1):
        return str(start.year)
    last = _shift(end, -1)
    if last == start:
        return f"{_MONTH_NAMES[start.month - 1]} {start.year}"
    return (
        f"{_MONTH_NAMES[start.month - 1]} {start.year} – {_MONTH_NAMES[last.month - 1]} {last.year}"
    )


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _series(result: Result) -> str | None:
    """With a date dimension and one other, the other one splits a line into series."""
    if len(result.dimensions) != 2:
        return None
    dated = [
        all(isinstance(row[i], str) and _DATE.match(row[i]) for row in result.rows)
        for i in range(2)
    ]
    if dated.count(True) != 1 or not result.rows:
        return None
    return result.dimensions[dated.index(False)]


def query_view(ref: str, result: Result) -> QueryView:
    """Chart points use the first metric. Each point links to its traced fact.

    A point's label holds the raw keys (``identity_theft · 2024-01-01``): the web splits
    and sorts them, and shows each one with its display label.
    """
    points = []
    if result.metric_meta:
        metric = next(iter(result.metric_meta))
        at = [result.columns.index(d) for d in result.dimensions]
        keys = [" · ".join(str(row[i]) for i in at) for row in result.rows]
        facts = [f for f in to_facts(result, ref) if f.metric == metric]
        for fact, raw in zip(facts, keys):
            label = raw if at else fact.label
            points.append(
                Point(label=label, value=fact.value, formatted=fact.formatted, fact_id=fact.id)
            )
    return QueryView(
        columns=result.columns,
        rows=result.rows,
        query_hash=result.query_hash,
        points=points,
        series=_series(result),
        labels=result.labels,
    )
