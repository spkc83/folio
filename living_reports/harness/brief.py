"""The data brief: what the writer model reads about one section.

It states what was counted, shows the data as one small table, and lists the candidate
claims that code proved. Nothing is cut silently: a long series rolls up (month -> quarter
-> half -> year), many members keep the top ones and an "Other" row, and the table says so.
"""
from datetime import date

import duckdb
from pydantic import BaseModel

from living_reports.engine import Query
from living_reports.engine.query import flat_filters
from living_reports.harness.claims import (
    _WORD,
    ADDITIVE,
    ClaimSet,
    Point,
    _arrow,
    _time_column,
    month_range,
    readable,
    value_label,
)
from living_reports.metrics.runner import Fact, Result, format_value

MAX_ROWS = 12
MAX_COLUMNS = 8
_OP = {"eq": "is", "in": "is", "ne": "is not", "not_in": "is not", "gt": "is above",
       "gte": "is at least", "lt": "is below", "lte": "is at most", "between": "is between",
       "is_null": "is empty", "contains": "contains"}


class ClaimOption(BaseModel):
    id: str
    text: str  # the claim with its values written out
    word: str | None = None  # the claim's finding word, for a {word:id} token
    values: list[str] = []  # "{fact:id} (6.08%)": the value tokens a sentence can use


class DataBrief(BaseModel):
    question: str
    counted: str
    table: str
    claims: list[ClaimOption]
    context: list[str] = []


def counted_text(
    query: Query,
    main: Result,
    labels: dict[str, str],
    period: tuple[date, date] | None,
    rows: int | None,
    noun: str = "rows",
) -> str:
    """The query in plain words, for example
    ``Fraud loss · Region is West · by Fraud type and month · Jan 2024 – Nov 2025 · 280,536 orders``.
    ``labels`` gives the label of each filtered member."""
    names = {**main.labels, **labels}
    parts = [", ".join(m["label"] for m in main.metric_meta.values())]
    for f in flat_filters(query.filters):
        values = " or ".join(value_label(f.member, v) for v in f.values)
        parts.append(f"{names.get(f.member, f.member)} {_OP.get(f.op, f.op)} {values}".strip())
    by = [names.get(d, d) for d in query.dimensions]
    if query.is_time_series:
        by.append(_WORD[query.time.grain][0])
    if by:
        parts.append("by " + " and ".join(by))
    if period is not None:
        parts.append(month_range(*period))
    if rows is not None:
        parts.append(f"{rows:,} {noun}")
    return " · ".join(parts)


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _grid(note: str, header: list[str], body: list[list[str]]) -> str:
    return "\n".join([note, "", _row(header), _row(["---"] * len(header)), *map(_row, body)])


def _fmt(main: Result, metric: str):
    fmt = main.metric_meta[metric]["format"]
    return lambda v: format_value(v, fmt)


def _rolled_note(points: list[Point], grain: str, months: int, metric_label: str) -> str:
    note = f"{metric_label} by {_WORD[grain][0]}"
    if grain != "month" and months:
        note += f" (rolled up from {months} months)"
    return note


def _member_table(query: Query, main: Result) -> str:
    """Members by the metrics, largest first: the top ones and one "Other" row."""
    metrics = list(main.metric_meta)[: MAX_COLUMNS - len(query.dimensions)]
    first = main.columns.index(metrics[0])
    dims = [main.columns.index(d) for d in query.dimensions]
    con = duckdb.connect()
    try:
        con.register("t", _arrow(main))
        ordered = con.execute(f"SELECT * FROM t ORDER BY c{first} DESC NULLS LAST").fetchall()
        n = len(ordered)
        additive = all(main.metric_meta[m].get("agg") in ADDITIVE for m in metrics)
        keep = MAX_ROWS - 1 if n > MAX_ROWS and additive else MAX_ROWS
        rest = None
        if n > keep and additive:
            sums = ", ".join(f"sum(CAST(c{main.columns.index(m)} AS DOUBLE))" for m in metrics)
            rest = con.execute(
                f"SELECT {sums} FROM (SELECT * FROM t ORDER BY c{first} DESC NULLS LAST OFFSET {keep})"
            ).fetchone()
    finally:
        con.close()
    fmts = [_fmt(main, m) for m in metrics]
    body = [
        [*(value_label(main.columns[d], row[d]) for d in dims),
         *(f(row[main.columns.index(m)]) for f, m in zip(fmts, metrics))]
        for row in ordered[:keep]
    ]
    if rest is not None:
        body.append([f"Other ({n - keep})", *[""] * (len(dims) - 1), *(f(v) for f, v in zip(fmts, rest))])
    header = [main.labels.get(d, d) for d in query.dimensions] + [main.labels.get(m, m) for m in metrics]
    note = f"{', '.join(header[len(dims):])} by {' and '.join(header[:len(dims)])}"
    if n > keep:
        note += f" (top {keep} of {n}" + (", the rest in Other)" if rest is not None else ")")
    return _grid(note, header, body)


def _months(points: list[Point]) -> int:
    return sum(p.months for p in points)


def _series_table(main: Result, metric: str, claims: ClaimSet) -> str:
    f = _fmt(main, metric)
    points = claims.total_series[-MAX_ROWS:]
    label = main.metric_meta[metric]["label"]
    note = _rolled_note(points, claims.grain or "month", _months(claims.total_series), label)
    if len(claims.total_series) > len(points):
        note += f" (the last {len(points)} of {len(claims.total_series)})"
    return _grid(note, [_WORD[claims.grain or "month"][0].capitalize(), label],
                 [[p.label, f(p.value)] for p in points])


def _pivot_table(query: Query, main: Result, metric: str, claims: ClaimSet) -> str:
    """Members (rows) by periods (columns). "Other" folds members only, never periods.

    An additive metric is rolled up and has a total column. Another metric shows its values
    as they are, in its last periods, with no total and no "Other" row."""
    f = _fmt(main, metric)
    additive = claims.member_additive
    series = claims.member_series
    if additive:
        rank = {m: sum(p.value or 0 for p in pts) for m, pts in series.items()}
    else:
        rank = {m: next((p.value for p in reversed(pts) if p.value is not None), 0) or 0
                for m, pts in series.items()}
    order = sorted(rank, key=lambda m: -rank[m])
    starts: dict[str, date] = {}
    for pts in series.values():
        for p in pts:
            starts[p.label] = min(starts.get(p.label, p.first), p.first)
    periods = sorted(starts, key=starts.get)
    all_periods = len(periods)
    if not additive:
        periods = periods[-(MAX_COLUMNS - 2):]
    room = MAX_ROWS - 1 if additive and len(order) > MAX_ROWS else MAX_ROWS
    keep = order[:room]

    def cells(names: list[str]) -> list[str]:
        values: dict[str, float | None] = {lab: None for lab in periods}
        for name in names:
            for p in series[name]:
                if p.label in values and p.value is not None:
                    values[p.label] = (values[p.label] or 0) + p.value
        row = [f(values[lab]) for lab in periods]
        return row + ([f(sum(rank[n] for n in names))] if additive else [])

    body = [[name, *cells([name])] for name in keep]
    if additive and len(order) > len(keep):
        body.append([f"Other ({len(order) - len(keep)})", *cells(order[len(keep):])])
    by = " and ".join(main.labels.get(d, d) for d in query.dimensions)
    grain = claims.member_grain or "month"
    one, many = _WORD[grain]
    note = f"{main.metric_meta[metric]['label']} by {by} and {one}"
    if additive and grain != "month":
        months = max((_months(p) for p in series.values()), default=0)
        note += f" (rolled up from {months} months)"
    if not additive:
        shown = f"the last {len(periods)} of {all_periods} {many}" if all_periods > len(periods) else f"all {many}"
        note += f" ({shown}; values as they are, not summed)"
    if len(order) > len(keep):
        note += f" (top {len(keep)} of {len(order)}" + (", the rest in Other)" if additive else ")")
    header = [by, *periods, *(["Total"] if additive else [])]
    return _grid(note, header, body)


def _totals_table(main: Result, facts: dict[str, Fact], ref: str, total_ref: str) -> str:
    rows = [[m["label"], facts[f"{total_ref}.{k}"].formatted]
            for k, m in main.metric_meta.items() if f"{total_ref}.{k}" in facts]
    return _grid("Totals", ["Measure", "Value"], rows)


def table_text(query: Query, ref: str, results: dict[str, Result], claims: ClaimSet,
               facts: dict[str, Fact]) -> str:
    main = results[ref]
    metric = next(iter(main.metric_meta))
    broken = bool(query.dimensions) or query.is_time_series
    total_ref = f"{ref}_total" if broken else ref
    has_time = _time_column(main) is not None
    if query.dimensions and has_time and claims.member_series:
        return _pivot_table(query, main, metric, claims)
    if query.dimensions:
        return _member_table(query, main)
    tables = [_totals_table(main, facts, ref, total_ref)] if f"{total_ref}.{metric}" in facts else []
    if claims.total_series:
        tables.append(_series_table(main, metric, claims))
    return "\n\n".join(tables)


def build_brief(
    question: str,
    query: Query,
    ref: str,
    results: dict[str, Result],
    claims: ClaimSet,
    facts: list[Fact],
    counted: str = "",
    context: list[str] | None = None,
) -> DataBrief:
    known = claims.facts(facts)
    return DataBrief(
        question=question,
        counted=counted,
        table=table_text(query, ref, results, claims, known),
        claims=[_option(c, known) for c in claims.claims],
        context=context or [],
    )


_VALUE_KEYS = ("fact", "first", "last", "pct", "target")


def _option(claim, known: dict[str, Fact]) -> ClaimOption:
    ids = [claim.params[k] for k in _VALUE_KEYS if isinstance(claim.params.get(k), str)]
    points = claim.params.get("points") or []
    ids += [points[0], points[-1]] if claim.word and points else []  # a trend: its ends
    values = [f"{{fact:{i}}} ({known[i].formatted})" for i in ids if i in known]
    return ClaimOption(id=claim.id, text=readable(claim.text, known), word=claim.word, values=values)


def _claim_line(c: ClaimOption) -> str:
    line = f"- {c.id}: {c.text}"
    if c.word:
        line += f'\n  word: {{word:{c.id}}} gives "{c.word}"'
    if c.values:
        line += f"\n  values: {', '.join(c.values)}"
    return line


def prompt_text(brief: DataBrief) -> str:
    lines = [
        f"The question: {brief.question}",
        f"What was counted: {brief.counted}",
        "",
        brief.table,
        "",
        "Claims (each one is checked against the data):",
        *(_claim_line(c) for c in brief.claims),
    ]
    if brief.context:
        lines += ["", "Earlier sections:", *(f"- {line}" for line in brief.context)]
    return "\n".join(lines)
