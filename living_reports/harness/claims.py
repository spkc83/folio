"""Claims: typed statements that code computes, checks, and renders.

The model does not write a finding. It cites a claim as ``{claim:id}``, and code writes the
claim's words from the facts. A claim kind has one template, and its words follow from the
values: "rose in every half-year" only when every step rises. ``check`` derives the text
again from the facts, so a claim that the data no longer supports is found on a refresh.

Example (West fraud loss by half-year: $2.11M, $2.34M, $2.22M, $2.63M):
"Fraud loss for West rose in 2 of 3 half-years: {fact:..2024-H1} in 2024 H1, ...".
"""
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import duckdb
import pyarrow as pa

from living_reports.engine import Query
from living_reports.engine.query import flat_filters
from living_reports.harness.decision_route import asks_movement
from living_reports.metrics.runner import Binding, Fact, Result, format_value
from living_reports.semantic.labels import display_value
from living_reports.spec.models import (
    Action,
    Claim,
    Evidence,
    EvidenceRow,
    Prose,
    ReportSpec,
    Section,
    SeriesPoint,
)
from living_reports.spec.validate import ANY_CLAIM
from living_reports.spec.validate import CLAIM as CLAIM_TOKEN
from living_reports.spec.validate import TOKEN as FACT_TOKEN
from living_reports.spec.validate import WORD as WORD_TOKEN

# The vague words, defined once. Templates use only these words.
FLAT = 0.02  # "flat": a change within 2% either way
SHARP = 0.20  # "rose sharply" / "fell sharply": a change of 20% or more
PEAK = 2.0  # a peak month: at least twice the median of the other months
MIN_PEAK_MONTHS = 6

MAX_CLAIMS = 40
MAX_SERIES = 6  # the points in a change or a trend
MAX_POPULATION = 200  # rank and extremes compare at most this many members
TOP = 5  # members that get a share and a rank claim
ADDITIVE = {"sum", "count"}
# A member of a breakdown of these is large mostly because it is big, so "best" and "worst"
# between members apply only to the other aggregations (rates and averages).
SIZED = {"sum", "count", "count_distinct"}
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Months in each grain, and its words.
_SPAN = {"day": 1, "week": 1, "month": 1, "quarter": 3, "half": 6, "year": 12}
_WORD = {"month": ("month", "months"), "quarter": ("quarter", "quarters"),
         "half": ("half-year", "half-years"), "year": ("year", "years"),
         "week": ("week", "weeks"), "day": ("day", "days")}
_LADDER = ["month", "quarter", "half", "year"]
_PERIOD_SQL = {
    "month": "strftime(d, '%Y-%m')",
    "quarter": "CAST(year(d) AS VARCHAR) || '-Q' || CAST(quarter(d) AS VARCHAR)",
    "half": "CAST(year(d) AS VARCHAR) || '-H' || CASE WHEN month(d) <= 6 THEN '1' ELSE '2' END",
    "year": "CAST(year(d) AS VARCHAR)",
}


# --- labels -------------------------------------------------------------------------------


def _label(value: Any) -> str:
    """The fallback display label: ``bust_out`` -> ``Bust out``, ``2024-01-01`` -> ``Jan 2024``."""
    if value is None:
        return "None"
    text = str(value)
    if _ISO.match(text):
        day = date.fromisoformat(text[:10])
        return f"{_MONTHS[day.month - 1]} {day.year}"
    text = text.replace("_", " ")
    return text[:1].upper() + text[1:] if text.islower() else text


def value_label(member: str | None, value: Any) -> str:
    """The label people read for a member value: ``Dealer collusion``, ``No fraud``, ``Jan 2024``."""
    return display_value(member, value) if member else _label(value)


def month_range(first: date, last: date) -> str:
    """``Jan 2024 – Nov 2025``; one month gives ``Nov 2025``."""
    a = f"{_MONTHS[first.month - 1]} {first.year}"
    b = f"{_MONTHS[last.month - 1]} {last.year}"
    return a if a == b else f"{a} – {b}"


def _month_end(day: date) -> date:
    nxt = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _slug(value: Any) -> str:
    return re.sub(r"[^\w.-]", "_", str(value)) if value is not None else "none"


def readable(text: str, facts: dict[str, Fact]) -> str:
    """Text with each fact token written as its value."""
    return FACT_TOKEN.sub(lambda m: facts[m.group(1)].formatted if m.group(1) in facts else "?", text)


# These kinds start with the metric ("Revenue for North rose…"); the others start with a member.
SUBJECT_FIRST = frozenset({"value", "change", "trend", "target", "peak"})


def inline_text(text: str, kind: str) -> str:
    """A claim's text for the middle of a sentence: "Over time, revenue for North rose…".

    Only a claim that starts with the metric gets a lower-case first letter. A member name
    ("North had…") and an acronym ("AOV was…") keep their capitals."""
    if kind not in SUBJECT_FIRST or len(text) < 2 or not text[1].islower():
        return text
    return text[0].lower() + text[1:]


def mid_sentence(before: str) -> bool:
    """True when a token comes after words of the same sentence ("Over time, {claim:…}")."""
    before = before.rstrip()
    return bool(before) and before[-1] not in ".!?"


def render_claims(text: str, claims: dict[str, Claim]) -> str:
    """Text with each claim token written as the claim's text (which keeps its fact tokens),
    and each word token as the claim's word."""
    text = WORD_TOKEN.sub(
        lambda m: claims[m.group(1)].word or m.group(0) if m.group(1) in claims else m.group(0), text)

    def claim(m: re.Match) -> str:
        c = claims.get(m.group(1))
        if c is None:
            return m.group(0)
        return inline_text(c.text, c.kind) if mid_sentence(m.string[:m.start()]) else c.text

    return CLAIM_TOKEN.sub(claim, text)


# --- derivation: one function for each kind ------------------------------------------------


def _ok(facts: dict[str, Fact], *ids: str | None) -> bool:
    return all(
        i is None or (i in facts and facts[i].state == "ok" and isinstance(facts[i].value, int | float))
        for i in ids
    )


def _t(fact_id: str) -> str:
    return f"{{fact:{fact_id}}}"


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _judged(up: bool, better: str | None) -> str | None:
    """The word "better" or "worse" for a value that went (or sits) up, from the direction."""
    if better is None:
        return None
    return "better" if up == (better == "higher") else "worse"


def _be(p: dict) -> str:
    """"were" for a plural subject ("Orders were 44,782"), else "was"."""
    return "were" if p.get("plural") else "was"


def _plural_label(label: str) -> bool:
    """``Orders`` and ``Dealers`` are plural; ``Fraud loss`` and ``Fraud rate`` are not."""
    last = label.split()[-1].lower() if label.split() else ""
    return last.endswith("s") and not last.endswith(("ss", "us", "is"))


def _share_part(p: dict, facts: dict[str, Fact], of_total: bool) -> str | None:
    share = p.get("share")
    if share is None:
        return ""
    if not _ok(facts, share):
        return None
    if of_total and p.get("total"):
        return f", {_t(share)} of {_t(p['total'])}" if _ok(facts, p["total"]) else None
    return f", {_t(share)} of the total"


def _value(p: dict, facts: dict[str, Fact]) -> str | None:
    if not _ok(facts, p["fact"]):
        return None
    period = f" ({p['period']})" if p.get("period") else ""
    return f"{p['subject']} {_be(p)} {_t(p['fact'])}{period}"


def _share(p: dict, facts: dict[str, Fact]) -> str | None:
    if not _ok(facts, p["fact"]) or not 0 <= facts[p["fact"]].value <= 1:
        return None
    return f"{p['member']} made up {_t(p['fact'])} of {p['whole']}"


def _rank(p: dict, facts: dict[str, Fact]) -> str | None:
    if not _ok(facts, p["fact"], *p["population"]):
        return None
    values = [facts[i].value for i in p["population"]]
    v = facts[p["fact"]].value
    above, n = sum(x > v for x in values), len(values)
    tied = sum(x == v for x in values) > 1
    share = _share_part(p, facts, of_total=False)
    if share is None:
        return None
    cited = f"({_t(p['fact'])}{share})"
    nouns, metric, where = p["nouns"], p["metric"], p.get("where", "")
    if tied:
        return f"{p['member']} was tied for {_ordinal(above + 1)} of the {n} {nouns} by {metric}{where} {cited}"
    if above in (0, n - 1):
        word = _top_word(above == 0, p.get("count", False))
        return f"{p['member']} had the {word} {metric} of the {n} {nouns}{where} {cited}"
    return f"{p['member']} ranked {_ordinal(above + 1)} of the {n} {nouns} by {metric}{where} {cited}"


def _extreme(p: dict, facts: dict[str, Fact]) -> str | None:
    if not _ok(facts, p["fact"], *p["population"]):
        return None
    v = facts[p["fact"]].value
    others = [facts[i].value for i in p["population"] if i != p["fact"]]
    if not others:
        return None
    if p["which"] == "max" and not all(v > x for x in others):
        return None
    if p["which"] == "min" and not all(v < x for x in others):
        return None
    share = _share_part(p, facts, of_total=True)
    if share is None:
        return None
    word = _top_word(p["which"] == "max", p.get("count", False))
    nouns = p.get("nouns") or _plural(p["noun"])
    return (f"{p['member']} had the {word} {p['metric']} of the {len(p['population'])} {nouns}"
            f"{p.get('where', '')} ({_t(p['fact'])}{share})")


def _top_word(top: bool, count: bool) -> str:
    """"The most leavers" for a count; "the highest revenue" for an amount, a rate, or an average."""
    if count:
        return "most" if top else "fewest"
    return "highest" if top else "lowest"


def _possessive(label: str) -> str:
    """"Home's", but "Electronics'" for a label that ends in s."""
    return f"{label}'" if label.endswith("s") else f"{label}'s"


def _compare(p: dict, facts: dict[str, Fact]) -> str | None:
    if not _ok(facts, p["a"], p["b"], p["ratio"]):
        return None
    a, b = facts[p["a"]].value, facts[p["b"]].value
    if a <= 0 or b <= 0 or not _close(facts[p["ratio"]].value, a / b):
        return None
    r = a / b
    head = f"{_possessive(p['a_label'])} {p['metric']} ({_t(p['a'])}) was"
    if abs(r - 1) <= FLAT:
        return f"{head} about the same as {_possessive(p['b_label'])} ({_t(p['b'])})"
    if r < 1:
        return None
    return f"{head} {_t(p['ratio'])} {_possessive(p['b_label'])} ({_t(p['b'])})"


def _change(p: dict, facts: dict[str, Fact]) -> str | None:
    if not _ok(facts, p["first"], p["last"], p["pct"]):
        return None
    a, b = facts[p["first"]].value, facts[p["last"]].value
    if a == 0 or not _close(facts[p["pct"]].value, (b - a) / abs(a)):
        return None
    d = (b - a) / abs(a)
    span = f"from {_t(p['first'])} in {p['first_label']} to {_t(p['last'])} in {p['last_label']}"
    if p.get("partial"):
        span += ", a partial period"
    if abs(d) <= FLAT:
        return f"{p['subject']} {_be(p)} flat (within 2%) {span}"
    # "Sharply" never spans a partial period: its total is not a like-for-like value.
    sharp = abs(d) >= SHARP and not p.get("partial")
    verb = ("rose" if d > 0 else "fell") + (" sharply" if sharp else "")
    judged = _judged(d > 0, p.get("better"))
    judgment = f", a change for the {judged}" if judged else ""
    if facts[p["first"]].formatted == facts[p["last"]].formatted:
        # "$1.0M to $1.0M" would read as no change: state the change and the rounding.
        tail = ", a partial period" if p.get("partial") else ""
        return (f"{p['subject']} {verb} {_t(p['pct'])}, from {p['first_label']} to "
                f"{p['last_label']}{tail} (both round to {_t(p['first'])}){judgment}")
    return f"{p['subject']} {verb} {span} ({_t(p['pct'])}){judgment}"


def _steps(values: list[float]) -> tuple[int, int, int]:
    ups = downs = flats = 0
    for a, b in zip(values, values[1:]):
        d = (b - a) / abs(a) if a else (0.0 if b == a else (1.0 if b > a else -1.0))
        if abs(d) <= FLAT:
            flats += 1
        elif d > 0:
            ups += 1
        else:
            downs += 1
    return ups, downs, flats


def _trend(p: dict, facts: dict[str, Fact]) -> str | None:
    if len(p["points"]) < 3 or not _ok(facts, *p["points"]):
        return None
    how = _trend_how(p, facts, judge=True)
    values = ", ".join(f"{_t(i)} in {label}" for i, label in zip(p["points"], p["labels"]))
    return f"{p['subject']} {how}: {values}"


def _trend_how(p: dict, facts: dict[str, Fact], judge: bool) -> str:
    """How a series moved: "rose in 2 of 3 half-years". ``judge`` adds the judgment of a
    series that moved one way in every step: ", each a change for the better"."""
    ups, downs, flats = _steps([facts[i].value for i in p["points"]])
    m = ups + downs + flats
    one, many = p["grain"]
    if ups == m or downs == m:
        how = f"{'rose' if ups == m else 'fell'} in every {one}"
        judged = _judged(ups == m, p.get("better")) if judge else None
        how += f", each a change for the {judged}" if judged else ""
    elif flats == m:
        how = f"{_be(p)} flat (within 2%) in every {one}"
    elif ups > downs:
        how = f"rose in {ups} of {m} {many}"
    elif downs > ups:
        how = f"fell in {downs} of {m} {many}"
    else:
        how = "went up and down"
    if flats and ups != m and downs != m and flats != m and ups != downs:
        # "fell in 1 of 5 months" alone hides that the other 4 steps were flat.
        how += f" and {_be(p)} flat (within 2%) in {flats}"
    return how


def _median(values: list[float]) -> float:
    v = sorted(values)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _spike(values: list[float], i: int) -> bool:
    """A month that falls back on both sides: each neighbor is at most half of it.

    A high first or last month, or two high months in a row, is a rise or a level, not a peak."""
    return 0 < i < len(values) - 1 and all(values[j] * PEAK <= values[i] for j in (i - 1, i + 1))


def _peak(p: dict, facts: dict[str, Fact]) -> str | None:
    """One month far above the others: "peaked at 5.64% in Aug 2025, 20.1× the median month"."""
    points = p["points"]
    if len(points) < MIN_PEAK_MONTHS or not _ok(facts, p["fact"], p["median"], p["ratio"], *points):
        return None
    v = facts[p["fact"]].value
    others = [facts[i].value for i in points if i != p["fact"]]
    if len(others) != len(points) - 1 or not all(v > x for x in others) or not _spike(
            [facts[i].value for i in points], points.index(p["fact"])):
        return None
    median = _median(others)
    if median <= 0 or v < PEAK * median or not _close(facts[p["median"]].value, median):
        return None
    if not _close(facts[p["ratio"]].value, v / median):
        return None
    return (f"{p['subject']} peaked at {_t(p['fact'])} in {p['label']}, {_t(p['ratio'])} "
            f"the median month ({_t(p['median'])})")


def _target(p: dict, facts: dict[str, Fact]) -> str | None:
    if not _ok(facts, p["fact"], p["target"]) or p.get("better") not in ("higher", "lower"):
        return None
    v, t = facts[p["fact"]].value, facts[p["target"]].value
    where = "below" if v < t else "above" if v > t else "at"
    text = f"{p['subject']} {_be(p)} {_t(p['fact'])}, {where} the target of {_t(p['target'])}"
    if v != t and facts[p["fact"]].formatted == facts[p["target"]].formatted:
        # "3.00%, above the target of 3.00%" reads as a contradiction: name the rounding.
        text += f" (both round to {_t(p['target'])})"
    return text


def misses_target(p: dict, facts: dict[str, Fact]) -> bool:
    """True when the value is on the wrong side of the target, from the better direction."""
    v, t = facts[p["fact"]].value, facts[p["target"]].value
    return v > t if p["better"] == "lower" else v < t


def word_of(kind: str, p: dict, facts: dict[str, Fact]) -> str | None:
    """The claim's finding word, for a ``{word:id}`` token in the writer's own sentence.

    Example: "North had the {word:q1.extreme.max.return_rate} return rate" renders "highest".
    A kind with no single word (a value, a share, a trend) gives ``None``."""
    if kind == "extreme":
        return _top_word(p["which"] == "max", p.get("count", False))
    if kind == "rank":
        values = [facts[i].value for i in p["population"]]
        v = facts[p["fact"]].value
        above, n = sum(x > v for x in values), len(values)
        if sum(x == v for x in values) > 1:
            return None
        count = p.get("count", False)
        if above in (0, n - 1):
            return _top_word(above == 0, count)
        return f"{_ordinal(above + 1)} {_top_word(True, count)}"
    if kind == "target":
        v, t = facts[p["fact"]].value, facts[p["target"]].value
        return "below" if v < t else "above" if v > t else "at"
    if kind == "change":
        a, b = facts[p["first"]].value, facts[p["last"]].value
        d = (b - a) / abs(a)
        if abs(d) <= FLAT:
            return "stayed flat"
        sharp = abs(d) >= SHARP and not p.get("partial")
        return ("rose" if d > 0 else "fell") + (" sharply" if sharp else "")
    if kind == "trend":
        return _trend_how(p, facts, judge=False)
    return None


_DERIVE: dict[str, Callable[[dict, dict[str, Fact]], str | None]] = {
    "value": _value, "share": _share, "rank": _rank, "extreme": _extreme,
    "compare": _compare, "change": _change, "trend": _trend, "target": _target, "peak": _peak,
}


def derive(kind: str, params: dict, facts: dict[str, Fact]) -> str | None:
    """The claim's text from the facts, or ``None`` when the facts do not support it."""
    return _DERIVE[kind](params, facts)


def check(claim: Claim, facts: dict[str, Fact]) -> bool:
    """True when the facts still give the claim's exact text."""
    return derive(claim.kind, claim.params, facts) == claim.text


# --- the data of a section ----------------------------------------------------------------


@dataclass
class Point:
    key: str  # 2024-H1
    label: str  # 2024 H1; a partial period names its months: Jul–Nov 2025
    first: date
    last: date  # the last day
    months: int  # the months of the period that the data covers
    value: float | None
    partial: bool = False  # the data does not fill the period


@dataclass(eq=False)
class Member:
    label: str
    keys: dict[str, str]
    fact: str
    value: float
    share: str | None = None


@dataclass
class ClaimSet:
    claims: list[Claim] = field(default_factory=list)
    derived: list[Fact] = field(default_factory=list)
    # Facts for the brief's table: the rolled-up series and the members, already formatted.
    total_series: list[Point] = field(default_factory=list)
    member_series: dict[str, list[Point]] = field(default_factory=dict)
    grain: str | None = None
    member_grain: str | None = None
    member_additive: bool = True
    period: tuple[date, date] | None = None

    def facts(self, base: list[Fact] | dict[str, Fact]) -> dict[str, Fact]:
        known = dict(base) if isinstance(base, dict) else {f.id: f for f in base}
        return known | {f.id: f for f in self.derived}


def _arrow(result: Result) -> pa.Table:
    columns = list(zip(*result.rows)) if result.rows else [()] * len(result.columns)
    return pa.table({f"c{i}": pa.array(list(col)) for i, col in enumerate(columns)})


def _time_column(result: Result) -> int | None:
    return next((i for i, c in enumerate(result.columns) if ":" in c), None)


def _period_label(grain: str, key: str, first: date, last: date, partial: bool) -> str:
    if grain in ("day", "week"):
        return key
    if grain == "month":
        return f"{_MONTHS[first.month - 1]} {first.year}"
    if not partial:
        year, _, part = key.partition("-")
        return {"quarter": f"{part} {year}", "half": f"{year} {part}", "year": key}[grain]
    a, b = _MONTHS[first.month - 1], _MONTHS[last.month - 1]
    return f"{a} {first.year}" if a == b else f"{a}–{b} {first.year}"


def _shift_month(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _period_start(grain: str, day: date) -> date:
    size = _SPAN.get(grain, 1)
    return date(day.year, (day.month - 1) // size * size + 1, 1)


def rolled(
    result: Result, metric: str, max_points: int, source_grain: str, additive: bool,
    members: list[int] | None = None, coverage: tuple[date, date] | None = None,
) -> tuple[str, dict[tuple, list[Point]]]:
    """The time series of ``metric`` (one per member key) at the finest grain that fits.

    An additive metric rolls up (month -> quarter -> half -> year) until each series has at
    most ``max_points``. Another metric cannot be summed: its series keeps its grain and its
    last ``max_points`` periods.
    """
    t = _time_column(result)
    v = result.columns.index(metric)
    keys = members or []
    key_sql = "".join(f"CAST(c{k} AS VARCHAR) AS k{n}, " for n, k in enumerate(keys))
    group = "".join(f"k{n}, " for n in range(len(keys)))
    con = duckdb.connect()
    try:
        con.register("t", _arrow(result))
        con.execute(
            f"CREATE TEMP TABLE s AS SELECT {key_sql}CAST(c{t} AS DATE) AS d, "
            f"CAST(c{v} AS DOUBLE) AS v FROM t WHERE c{t} IS NOT NULL"
        )
        per = 3 if source_grain == "quarter" else 12 if source_grain == "year" else 1
        ladder = _LADDER[_LADDER.index(source_grain):] if source_grain in _LADDER else _LADDER
        grain = ladder[0]
        if additive:
            counts = con.execute(
                "SELECT " + ", ".join(f"count(DISTINCT {_PERIOD_SQL[g]})" for g in ladder) + " FROM s"
            ).fetchone()
            grain = next((g for g, n in zip(ladder, counts) if n <= max_points), ladder[-1])
        if source_grain in ("day", "week") and not additive:
            grain = source_grain
        expr = _PERIOD_SQL.get(grain, "strftime(d, '%Y-%m-%d')")
        rows = con.execute(
            f"SELECT {group}{expr} AS p, min(d), max(d), "
            f"count(DISTINCT date_trunc('month', d)) * {per}, sum(v) "
            f"FROM s GROUP BY ALL ORDER BY ALL"
        ).fetchall()
        # The months of the whole series: a period that reaches past them is partial.
        lo_all, hi_all = con.execute(
            "SELECT min(date_trunc('month', d))::DATE, max(date_trunc('month', d))::DATE FROM s"
        ).fetchone()
        if coverage is not None:
            # Rows at a coarse grain carry the first day of each period: the months come from
            # the monthly series, so that "Oct–Nov 2025" is a partial quarter.
            lo_all, hi_all = coverage
    finally:
        con.close()
    out: dict[tuple, list[Point]] = {}
    width = len(keys)
    for row in rows:
        key, (p, lo, hi, months, value) = tuple(row[:width]), row[width:]
        if grain in ("quarter", "half", "year"):
            start = _period_start(grain, lo)
            end = _shift_month(start, _SPAN[grain] - 1)
            first, last_month = max(start, lo_all), min(end, hi_all)
            months = (last_month.year - first.year) * 12 + last_month.month - first.month + 1
            partial = months < _SPAN[grain]
            last = _month_end(last_month)
        else:
            first, partial = lo, False
            last = _month_end(hi) if grain not in ("day", "week") else hi
        label = _period_label(grain, p, first, last, partial)
        out.setdefault(key, []).append(Point(p, label, first, last, months, value, partial))
    if not additive:
        out = {k: pts[-max_points:] for k, pts in out.items()}
    return grain, out


# --- candidates ---------------------------------------------------------------------------


@dataclass
class _Section:
    query: Query
    ref: str
    results: dict[str, Result]
    facts: dict[str, Fact]
    question: str
    counted: str
    out: ClaimSet
    scored: list[tuple[float, Claim]] = field(default_factory=list)
    # Members that the writer proposed: they get the claims of a member that the question names.
    focus: tuple[str, ...] = ()

    def named(self, labels: list[str]) -> bool:
        return _named(self.question, labels) or any(x in self.focus for x in labels if x)

    @property
    def main(self) -> Result:
        return self.results[self.ref]


def _segment(query: Query) -> dict[str, str]:
    """The members that the filters fix to one value: ``{"dealers.region": "West"}``."""
    return {
        f.member: str(f.values[0])
        for f in flat_filters(query.filters)
        if f.op in ("eq", "in") and len(f.values) == 1
    }


def _where(segment: dict[str, str]) -> str:
    return " for " + ", ".join(value_label(m, v) for m, v in segment.items()) if segment else ""


def _named(question: str, labels: list[str]) -> bool:
    lowered = question.lower()
    return any(re.search(rf"\b{re.escape(x.lower())}\b", lowered) for x in labels if x)


def _derived(
    s: _Section, fid: str, label: str, value: float | None, metric: str, basis: str,
    keys: dict[str, str] | None = None, formatted: str | None = None,
) -> Fact:
    base = s.main
    meta = base.metric_meta[metric]
    text = formatted if formatted is not None else format_value(value, meta["format"])
    fact = Fact(
        id=fid, label=label, value=value if value is not None else None, formatted=text,
        query_hash=base.query_hash, metric=metric, metric_version=base.metric_versions.get(metric),
        source_id=base.source_id, data_time=base.data_time,
        state="ok" if value is not None else "empty",
        definition=meta.get("definition") or None,
        binding=Binding(ref=s.ref, metric=metric, keys=keys) if keys is not None else None,
        basis=basis,
    )
    s.out.derived.append(fact)
    s.facts[fid] = fact
    return fact


FOCUS_SCORE = 50  # a proposed member's claims rank below the section's leading claims
NARROWED_SCORE = 5000  # the largest move of the member that a narrowed section names: first


def _worse(better: str | None, change: float) -> bool:
    return (better == "higher" and change < 0) or (better == "lower" and change > 0)


def _lead_move(s: _Section, seg: dict[str, str]) -> None:
    """A section narrowed to one member that the question names, and broken down by another
    dimension ("Electronics by Brand", a follow-up chip), leads with that member's largest
    change. The earlier section stated the member's level; the person asks how it moves.
    A move in the wrong direction for its metric (a margin that falls, a loss that rises) comes
    before a move in the right direction: it is the move that needs an explanation.

    Example: "Gross margin for Electronics fell sharply from Q1 2025 to Q4 2025" comes before
    the brand with the highest revenue."""
    if not (s.query.dimensions and seg and _named(s.question, list(seg.values()))):
        return
    moves = [(_worse(c.params.get("better"), fact.value), abs(fact.value), n)
             for n, (_, c) in enumerate(s.scored)
             if c.kind == "change" and c.segment == seg
             and (fact := s.facts.get(c.params["pct"])) is not None and fact.value is not None]
    if moves:
        *_, n = max(moves)
        s.scored[n] = (NARROWED_SCORE, s.scored[n][1])


def _lead_mover(s: _Section, seg: dict[str, str]) -> None:
    """A question that asks how a value moves, by a dimension and over time, leads with the
    member that moved most. The trend of the total comes after it.

    Example: "How is revenue changing by channel?" leads with the channel whose revenue changed
    most, not with the total's trend: the person asks which member moves. A question that
    names a member keeps that member's claims first."""
    if not (s.query.dimensions and s.query.is_time_series and asks_movement(s.question)):
        return
    if (seg and _named(s.question, list(seg.values()))) or _named(s.question, list(s.out.member_series)):
        return
    moves = [(abs(fact.value), _worse(c.params.get("better"), fact.value), n)
             for n, (_, c) in enumerate(s.scored)
             if c.kind == "change" and c.segment != seg and not c.id.endswith(".partial")
             and (fact := s.facts.get(c.params["pct"])) is not None and fact.value is not None]
    if moves:
        *_, n = max(moves)
        s.scored[n] = (NARROWED_SCORE, s.scored[n][1])


def _add(s: _Section, score: float, kind: str, cid: str, params: dict, evidence: Evidence,
         segment: dict[str, str], period: tuple[date, date] | None) -> None:
    proposed = [v for v in segment.values() if v in s.focus]
    if proposed and not _named(s.question, proposed):
        # A member that the writer proposed gets claims, not the lead: the coverage rule
        # keeps the claims that state the section's story.
        score = min(score, FOCUS_SCORE)
    text = derive(kind, params, s.facts)
    if text is None:
        return
    about = [value_label(m, v) for m, v in segment.items()]
    if params.get("member"):
        about.append(params["member"])
    claim = Claim(
        id=cid, kind=kind, text=text, facts=list(dict.fromkeys(FACT_TOKEN.findall(text))),
        query_ref=s.ref, segment=segment, evidence=evidence, params=params,
        period=(period[0].isoformat(), period[1].isoformat()) if period else None,
        word=word_of(kind, params, s.facts), about=list(dict.fromkeys(about)),
    )
    s.scored.append((score, claim))


def _evidence(s: _Section, metric: str, by: str | None, rows=(), series=None) -> Evidence:
    return Evidence(
        counted=s.counted, by=by, rows=list(rows), series=series,
        definition=s.main.metric_meta[metric].get("definition") or None,
    )


def _neighbors(members: list[Member], marked: set[str]) -> list[EvidenceRow]:
    """All members when there are few; else the top four and each marked one with its neighbors."""
    keep = set(range(len(members))) if len(members) <= 8 else set(range(4))
    for i, m in enumerate(members):
        if m.fact in marked:
            keep |= {i - 1, i, i + 1}
    return [
        EvidenceRow(label=members[i].label, fact=members[i].fact, highlight=members[i].fact in marked)
        for i in sorted(k for k in keep if 0 <= k < len(members))
    ]


def _series_facts(s: _Section, metric: str, points: list[Point], grain: str, seg: dict[str, str],
                  slug: str) -> list[str]:
    """One derived fact per period. A rolled-up period says how many values it sums."""
    meta = s.main.metric_meta[metric]
    source = s.query.time.grain if s.query.is_time_series else "month"
    ids = []
    for p in points:
        fid = f"{s.ref}.derived.{metric}.{grain}.{slug}.{p.key}"
        if grain != source:
            basis = f"sum of {p.months // max(1, _SPAN.get(source, 1))} {_WORD[source][0]}ly values"
        else:
            basis = f"the {_WORD[grain][0]}ly value"
        basis = basis.replace("dayly", "daily")
        if p.partial:
            basis += f" (a partial {_WORD[grain][0]}: {p.label})"
        _derived(s, fid, f"{meta['label']}{_where(seg)} · {p.label}", p.value, metric, basis, keys=seg)
        ids.append(fid)
    return ids


def _change_claim(s: _Section, metric: str, points: list[Point], ids: list[str], grain: str,
                  subject: str, seg: dict[str, str], slug: str, score: float, cid: str) -> None:
    meta = s.main.metric_meta[metric]
    first, last = points[0], points[-1]
    if not first.value:
        return
    d = (last.value - first.value) / abs(first.value)
    pct = f"{s.ref}.derived.{metric}.change.{grain}.{slug}" + (".partial" if last.partial else "")
    _derived(s, pct, f"Change in {meta['label'].lower()}{_where(seg)}, {first.label} to {last.label}",
             d, metric, f"({last.label} - {first.label}) / {first.label}", formatted=f"{d * 100:+.1f}%")
    params = {"subject": subject, "plural": _plural_label(s.main.metric_meta[metric]["label"]), "first": ids[0], "last": ids[-1], "pct": pct,
              "first_label": first.label, "last_label": last.label, "partial": last.partial,
              "better": meta.get("better")}
    rows = [EvidenceRow(label=p.label, fact=i, highlight=n in (0, len(ids) - 1))
            for n, (p, i) in enumerate(zip(points, ids))]
    series = [SeriesPoint(label=p.label, fact=i, partial=p.partial) for p, i in zip(points, ids)]
    _add(s, score + 20 * min(abs(d), 1), "change", cid, params,
         _evidence(s, metric, _WORD[grain][0], rows, series), seg, (first.first, last.last))


def _series_claims(s: _Section, metric: str, points: list[Point], grain: str, subject: str,
                   seg: dict[str, str], slug: str, boost: float) -> None:
    """A change and a trend over the full periods, and a change that ends in a partial one.

    Example: West by half-year, 2024 H1 .. 2025 H1 full, Jul–Nov 2025 partial."""
    if any(p.value is None for p in points) or not any(p.value for p in points):
        return
    # A question about time puts its trend first; in a breakdown the members come first.
    boost += 20 if s.query.is_time_series else -10
    while points and points[0].partial:  # the data starts inside a period: drop it
        points = points[1:]
    full = [p for p in points if not p.partial]
    tail = points[-1] if points and points[-1].partial else None
    full = full if not tail else points[:-1]
    ids = _series_facts(s, metric, points, grain, seg, slug)
    full_ids = ids[: len(full)]
    if len(full) >= 2:
        _change_claim(s, metric, full, full_ids, grain, subject, seg, slug, boost + 45,
                      f"{s.ref}.change.{metric}.{slug}.{grain}")
    # A partial last period counts only when it covers at least half of its grain.
    if tail is not None and full and tail.months >= _SPAN.get(grain, 1) / 2:
        _change_claim(s, metric, [full[0], *full[1:], tail], [*full_ids, ids[-1]], grain, subject,
                      seg, slug, boost + 40, f"{s.ref}.change.{metric}.{slug}.{grain}.partial")
    if len(full) >= 3:
        labels = [p.label for p in full]
        params = {"subject": subject, "plural": _plural_label(s.main.metric_meta[metric]["label"]), "points": full_ids, "labels": labels, "grain": list(_WORD[grain]),
                  "better": s.main.metric_meta[metric].get("better")}
        rows = [EvidenceRow(label=p.label, fact=i, highlight=True) for p, i in zip(full, full_ids)]
        series = [SeriesPoint(label=p.label, fact=i, partial=p.partial) for p, i in zip(points, ids)]
        _add(s, boost + 70, "trend", f"{s.ref}.trend.{metric}.{slug}.{grain}", params,
             _evidence(s, metric, _WORD[grain][0], rows, series), seg, (full[0].first, full[-1].last))


def _peak_claim(s: _Section, metric: str, months: list[Point], subject: str, seg: dict[str, str],
                slug: str, score: float) -> None:
    """A peak over every month of the series, not only the last ones that a trend shows."""
    months = [p for p in months if p.value is not None]
    if len(months) < MIN_PEAK_MONTHS:
        return
    top = max(months, key=lambda p: p.value)
    others = [p.value for p in months if p is not top]
    median = _median(others)
    if median <= 0 or top.value < PEAK * median or any(x >= top.value for x in others):
        return
    if not _spike([p.value for p in months], months.index(top)):
        return
    meta = s.main.metric_meta[metric]
    ids = _series_facts(s, metric, months, "month", seg, slug)
    fact = ids[months.index(top)]
    base = f"{s.ref}.derived.{metric}.peak.{slug}"
    _derived(s, f"{base}.median", f"Median month of {meta['label'].lower()}{_where(seg)}", median,
             metric, "the median of the other months")
    r = top.value / median
    _derived(s, f"{base}.ratio", f"{meta['label']}{_where(seg)}: {top.label} to the median month", r,
             metric, f"{top.label} divided by the median month",
             formatted=f"{r:.1f}×" if r >= 1.1 else f"{r:.2f}×")
    params = {"subject": subject, "fact": fact, "points": ids, "median": f"{base}.median",
              "ratio": f"{base}.ratio", "label": top.label}
    rows = [EvidenceRow(label=p.label, fact=i, highlight=p is top) for p, i in zip(months, ids)]
    series = [SeriesPoint(label=p.label, fact=i) for p, i in zip(months, ids)]
    _add(s, score, "peak", f"{s.ref}.peak.{metric}.{slug}", params,
         _evidence(s, metric, "month", rows, series), seg, (months[0].first, months[-1].last))


def _plural(noun: str) -> str:
    """``region`` -> ``regions``, ``category`` -> ``categories``, ``day`` -> ``days``."""
    if noun.endswith("y") and noun[-2:-1] not in "aeiou":
        return noun[:-1] + "ies"
    return noun + ("es" if noun.endswith(("s", "x", "ch", "sh")) else "s")


def _target_fact(s: _Section, metric: str) -> str | None:
    """The id of the derived fact that holds the metric's target, made once per section.

    ``None`` when the metric has no target, or when it adds up (a target cannot fit a total)."""
    meta = s.main.metric_meta[metric]
    if meta.get("target") is None or meta.get("better") is None or meta.get("agg") in ADDITIVE:
        return None
    fid = f"{s.ref}.derived.{metric}.target"
    if fid not in s.facts:
        _derived(s, fid, f"{meta['label']} target", float(meta["target"]), metric,
                 "the target in the metric definition")
    return fid


def _target_params(s: _Section, metric: str, subject: str, fact: str, target: str) -> dict:
    return {"subject": subject, "plural": _plural_label(s.main.metric_meta[metric]["label"]), "fact": fact, "target": target,
            "better": s.main.metric_meta[metric]["better"]}


def _population_claims(s: _Section, metric: str, members: list[Member], by: str, total: str | None,
                       seg: dict[str, str]) -> None:
    """Shares, ranks, extremes, and one comparison over the members of a breakdown."""
    meta = s.main.metric_meta[metric]
    label = meta["label"]
    metric_l = label[:1].lower() + label[1:]
    # "the best result" only between members of a rate or an average, not of a total.
    better = None if meta.get("agg") in SIZED else meta.get("better")
    count = meta.get("format") == "count"
    members = sorted(members, key=lambda m: -m.value)
    n = len(members)
    if n < 2:
        return
    if len(s.query.dimensions) > 1:
        # "the 20 combinations of Region and Fraud type", not "the 20 region and Fraud types".
        noun, nouns = f"combination of {by}", f"combinations of {by}"
    else:
        noun = by[:1].lower() + by[1:]
        nouns = _plural(noun)
    where = _where(seg)
    population = [m.fact for m in members][:MAX_POPULATION]
    named = [m for m in members if s.named([m.label, *m.keys.values()])]
    marked = {m.fact for m in named}
    extremes = []
    for which, m in (("max", members[0]), ("min", members[-1])):
        if n > MAX_POPULATION:
            break
        if which == "min" and m.value == 0 and m not in named:
            continue  # "No fraud had the lowest fraud loss ($0.00)" says nothing
        extremes.append(m)
        params = {"member": m.label, "fact": m.fact, "share": m.share, "total": total,
                  "population": population, "which": which, "noun": noun, "nouns": nouns,
                  "metric": metric_l, "where": where, "better": better, "count": count}
        # With no named member, the breakdown's leader comes before the plain total (80).
        asked = any(_named(s.question, [x.label, *x.keys.values()]) for x in named)
        base = (85 if not asked else 70) if which == "max" else 45
        # A named member at either end leads the section, in place of its rank claim.
        score = 1060 if m.fact in marked else base
        _add(s, score, "extreme", f"{s.ref}.extreme.{which}.{metric}", params,
             _evidence(s, metric, by, _neighbors(members, {m.fact})), seg | m.keys, s.out.period)
    # Only a strict end gets an extreme claim; a tied member keeps its rank ("tied for 1st").
    ends = {m.fact for m in extremes
            if sum(x.value == m.value for x in members) == 1}
    if n <= MAX_POPULATION:
        for m in dict.fromkeys([*named, *members[1:TOP]]):
            if m.fact in ends or (m is members[-1] and m not in named):
                continue  # the extreme claim already says "the highest" or "the lowest"
            params = {"member": m.label, "fact": m.fact, "share": m.share, "population": population,
                      "nouns": nouns, "metric": metric_l, "where": where, "better": better,
                      "count": count}
            # A named member's rank leads the section: "West had the lowest fraud loss of the 4 regions".
            score = 1060 if m in named else 30
            _add(s, score, "rank", f"{s.ref}.rank.{metric}.{_slug(m.label)}", params,
                 _evidence(s, metric, by, _neighbors(members, {m.fact})), seg | m.keys, s.out.period)
    if (target := _target_fact(s, metric)) is not None:
        for m in members:
            p = _target_params(s, metric, f"{label}{_where(seg | m.keys)}", m.fact, target)
            if not derive("target", p, s.facts):
                continue
            if m not in named and not misses_target(p, s.facts):
                continue  # a member that meets the target is news only when the question names it
            score = 1070 if m in named else 90  # a miss leads, above the breakdown leader (85)
            _add(s, score, "target", f"{s.ref}.target.{metric}.{_slug(m.label)}", p,
                 _evidence(s, metric, by, [*_neighbors(members, {m.fact}),
                                           EvidenceRow(label="Target", fact=target)]),
                 seg | m.keys, s.out.period)
    whole = f"{metric_l}{where}"
    for m in dict.fromkeys([*named, *members[:TOP]]):
        if m.share is None:
            continue
        value = s.facts[m.share].value if m.share in s.facts else None
        if not value and m not in named:
            continue
        score = (1000 if m in named else 0) + 20 + 50 * (value or 0)
        _add(s, score, "share", f"{s.ref}.share.{metric}.{_slug(m.label)}",
             {"member": m.label, "fact": m.share, "whole": whole},
             _evidence(s, metric, by, _neighbors(members, {m.fact})), seg | m.keys, s.out.period)
    if n > MAX_POPULATION:
        return
    other = next((m for m in named if m is not members[0]), members[1])
    top = members[0]
    if other.value > 0:
        ratio = f"{s.ref}.derived.{metric}.ratio.{_slug(top.label)}.{_slug(other.label)}"
        r = top.value / other.value
        _derived(s, ratio, f"{label}: {top.label} to {other.label}", r, metric,
                 f"{top.label} divided by {other.label}",
                 formatted=f"{r:.1f}×" if r >= 1.1 else f"{r:.2f}×")
        params = {"a": top.fact, "b": other.fact, "ratio": ratio, "a_label": top.label,
                  "b_label": other.label, "metric": metric_l}
        score = 1020 if other in named else 55
        _add(s, score, "compare", f"{s.ref}.compare.{metric}.{_slug(top.label)}.{_slug(other.label)}",
             params, _evidence(s, metric, by, _neighbors(members, {top.fact, other.fact})),
             seg | other.keys, s.out.period)


def _row_members(s: _Section, metric: str) -> list[Member]:
    """The members of a breakdown with no time column: one fact per row."""
    prefix = f"{s.ref}.{metric}."
    out = []
    for fact in s.facts.values():
        if not fact.id.startswith(prefix) or fact.binding is None or fact.binding.share:
            continue
        if fact.state != "ok" or not isinstance(fact.value, int | float):
            continue
        keys = {k: v for k, v in fact.binding.keys.items() if v is not None}
        label = " · ".join(value_label(k, v) for k, v in keys.items())
        share = f"{s.ref}.{metric}.share.{fact.id[len(prefix):]}"
        out.append(Member(label, keys, fact.id, float(fact.value), share if share in s.facts else None))
    return out


def _grain_of(result: Result) -> str | None:
    t = _time_column(result)
    return result.columns[t].rsplit(":", 1)[-1] if t is not None else None


def _coverage(results: dict[str, Result], ref: str) -> tuple[date, date] | None:
    """The first and the last month that the section's data has."""
    source = results.get(f"{ref}_months") or results[ref]
    t = _time_column(source)
    if t is None or _grain_of(source) != "month":
        return None
    days = [date.fromisoformat(str(r[t])[:10]) for r in source.rows if r[t] is not None]
    return (min(days), max(days)) if days else None


def _query_bounds(query: Query) -> tuple[date, date] | None:
    """The inclusive months that the query asks for: both bounds, or none."""
    t = query.time
    if t is None or t.start is None or t.end is None:
        return None
    return t.start, t.end - timedelta(days=1)


def _data_period(s: _Section, points: list[Point]) -> tuple[date, date] | None:
    t = s.query.time
    lo = t.start if t else None
    hi = t.end - timedelta(days=1) if t and t.end else None
    if points:
        lo = lo or points[0].first
        hi = hi or points[-1].last
    return (lo, hi) if lo and hi else None


def _member_series_claims(s: _Section, metric: str, member_cols: list[int], source: str,
                          additive: bool, limited: bool, total_id: str, seg: dict[str, str]) -> None:
    """A breakdown over time: one series per member, and claims over the members.

    An additive metric rolls up and sums to member totals (for ranks and shares). Another
    metric keeps each member's own values, as they are; its series keeps its last periods."""
    main, out, meta = s.main, s.out, s.main.metric_meta[metric]
    where = _where(seg)
    width = MAX_SERIES if additive else 10**6
    rollup = s.results.get(f"{s.ref}_member_rollup")
    if not additive and rollup is not None and metric in rollup.columns:
        cols = [i for i, c in enumerate(rollup.columns) if c in s.query.dimensions]
        grain_m, series = rolled(rollup, metric, MAX_SERIES, _grain_of(rollup), False, cols,
                                 coverage=_coverage(s.results, s.ref))
    else:
        # Rows by quarter or year carry the first day of each period: the months that the data
        # covers come from the monthly series, so that "Jan–Nov 2025" is a partial year.
        coverage = _coverage(s.results, s.ref) if source in ("quarter", "year") else None
        grain_m, series = rolled(main, metric, width, source, additive, member_cols, coverage)
    out.member_grain, out.member_additive = grain_m, additive
    total = s.facts.get(total_id)
    members: list[tuple[Member, list[Point]]] = []
    for key, points in series.items():
        keys = {main.columns[c]: v for c, v in zip(member_cols, key)}
        label = " · ".join(value_label(k, v) for k, v in keys.items())
        out.member_series[label] = points
        slug = _slug("_".join(str(v) for v in key))
        if additive:
            value = sum(p.value or 0 for p in points)
            fid = f"{s.ref}.derived.{metric}.total.{slug}"
            _derived(s, fid, f"{meta['label']}{where} · {label}", value, metric,
                     f"sum of the {_WORD[grain_m][0]} values", keys=seg | keys)
            share = None
            if total is not None and total.value:
                share = f"{s.ref}.derived.{metric}.share.{slug}"
                _derived(s, share, f"Share of {meta['label'].lower()}{where} · {label}",
                         value / total.value, metric, f"{label} divided by the total",
                         formatted=format_value(value / total.value, "percent"))
            members.append((Member(label, keys, fid, value, share), points))
        else:
            last = next((p.value for p in reversed(points) if p.value is not None), 0.0)
            members.append((Member(label, keys, "", float(last)), points))
    if source == "month":
        _, by_month = rolled(main, metric, 10**6, "month", additive, member_cols)
        for key, points in by_month.items():
            keys = {main.columns[c]: v for c, v in zip(member_cols, key)}
            label = " · ".join(value_label(k, v) for k, v in keys.items())
            named = s.named([label, *keys.values()])
            subject = f"{meta['label']} for {', '.join([*(value_label(k, v) for k, v in seg.items()), label])}"
            _peak_claim(s, metric, points, subject, seg | keys,
                        _slug("_".join([*seg.values(), *keys.values()])), 1060 if named else 60)
    by = " and ".join(main.labels.get(d, d) for d in s.query.dimensions)
    if additive and not limited:
        _population_claims(s, metric, [m for m, _ in members], by,
                           total_id if total is not None else None, seg)
    for m, points in sorted(members, key=lambda mp: -mp[0].value)[:3]:
        boost = 1000 if s.named([m.label, *m.keys.values()]) else -40
        subject = f"{meta['label']} for {', '.join([*(value_label(k, v) for k, v in seg.items()), m.label])}"
        _series_claims(s, metric, points[-MAX_SERIES:], grain_m, subject, seg | m.keys,
                       _slug("_".join([*seg.values(), *m.keys.values()])), boost)


def _total_subject(query: Query, main: Result, meta: dict, where: str) -> str | None:
    """"Total return rate across all regions" for the total of a breakdown. None: no breakdown."""
    if not query.dimensions:
        return None
    by = " and ".join(main.labels.get(d, d) for d in query.dimensions)
    whole = f"combinations of {by}" if len(query.dimensions) > 1 else _plural(by[:1].lower() + by[1:])
    label = meta["label"]
    return f"Total {label[:1].lower() + label[1:]}{where} across all {whole}"


def claims_for(
    query: Query,
    ref: str,
    results: dict[str, Result],
    facts: list[Fact],
    question: str = "",
    counted: str = "",
    focus: list[str] | tuple[str, ...] = (),
) -> ClaimSet:
    """The candidate claims of a section, all true by construction, most salient first.

    ``focus`` holds member labels that the writer proposed: each gets the claims of a member
    that the question names (rank, share, compare). A label that is no member does nothing.

    Also returns the derived facts that the claims cite (half-year totals, ratios, changes).
    """
    out = ClaimSet()
    s = _Section(query, ref, results, {f.id: f for f in facts}, question, counted, out,
                 focus=tuple(focus))
    main = s.main
    seg = _segment(query)
    where = _where(seg)
    broken = bool(query.dimensions) or query.is_time_series
    total_ref = f"{ref}_total" if broken else ref
    time = _time_column(main)
    member_cols = [i for i, c in enumerate(main.columns) if c in query.dimensions]
    source = query.time.grain if query.is_time_series else "month"
    limited = query.limit is not None or main.truncated or bool(query.having)

    for n, metric in enumerate(main.metric_meta):
        meta = main.metric_meta[metric]
        additive = meta.get("agg") in ADDITIVE
        subject = f"{meta['label']}{where}"
        # The section's own series: the monthly results, the main series, or the members summed.
        total_points: list[Point] = []
        grain = None
        rollup = results.get(f"{ref}_rollup")
        if not additive and rollup is not None and metric in rollup.columns:
            # A rate cannot be summed: the engine computed it at a coarser grain.
            grain, by_key = rolled(rollup, metric, MAX_SERIES, _grain_of(rollup), False,
                                   coverage=_coverage(results, ref))
            total_points = by_key.get((), [])
        elif f"{ref}_months" in results and metric in results[f"{ref}_months"].columns:
            grain, by_key = rolled(results[f"{ref}_months"], metric, MAX_SERIES, "month", additive)
            total_points = by_key.get((), [])
        elif time is not None and (not member_cols or additive):
            grain, by_key = rolled(main, metric, MAX_SERIES, source, additive)
            total_points = by_key.get((), [])
        if n == 0:
            out.grain, out.total_series = grain, total_points
            out.period = _data_period(s, total_points)
        period = out.period
        total_id = f"{total_ref}.{metric}"
        if total_id in s.facts:
            # Only the query's own bounds name a period in the text. The data's span moves
            # with new data, and the claim's words must not change when only the value does.
            bounds = _query_bounds(query)
            params = {"subject": subject, "plural": _plural_label(meta["label"]), "fact": total_id,
                      "period": month_range(*bounds) if bounds else None}
            _add(s, 80 - n, "value", f"{ref}.value.{metric}", params,
                 _evidence(s, metric, None, [EvidenceRow(label=subject, fact=total_id, highlight=True)]),
                 seg, period)
            if (target := _target_fact(s, metric)) is not None:
                _add(s, 82 - n, "target", f"{ref}.target.{metric}",
                     _target_params(s, metric, _total_subject(query, main, meta, where) or subject,
                                    total_id, target),
                     _evidence(s, metric, None, [EvidenceRow(label=subject, fact=total_id, highlight=True),
                                                 EvidenceRow(label="Target", fact=target)]),
                     seg, period)
        if total_points and grain:
            _series_claims(s, metric, total_points, grain, subject, seg, _slug("_".join(seg.values()) or "all"), 0)
        monthly = results.get(f"{ref}_months") if f"{ref}_months" in results else (
            main if time is not None and not member_cols and source == "month" else None)
        if monthly is not None and metric in monthly.columns:
            _, by_month = rolled(monthly, metric, 10**6, "month", additive)
            _peak_claim(s, metric, by_month.get((), []), subject, seg,
                        _slug("_".join(seg.values()) or "all"), 95)
        if n > 0:
            continue  # breakdown claims use the first metric
        by = " and ".join(main.labels.get(d, d) for d in query.dimensions)
        if member_cols and time is None:
            members = _row_members(s, metric)
            if not limited:
                _population_claims(s, metric, members, by, total_id if total_id in s.facts else None, seg)
        elif member_cols and time is not None:
            _member_series_claims(s, metric, member_cols, source, additive, limited, total_id, seg)

    _lead_move(s, seg)
    _lead_mover(s, seg)
    ranked = sorted(s.scored, key=lambda sc: -sc[0])
    out.claims = list({c.id: c for _, c in ranked}.values())[:MAX_CLAIMS]
    # Keep only derived facts that a kept claim cites.
    cited = {f for c in out.claims for f in c.facts} | {
        r.fact for c in out.claims for r in c.evidence.rows
    } | {p.fact for c in out.claims for p in (c.evidence.series or [])}
    for c in out.claims:  # the ids that a derivation reads, cited in the text or not
        for v in c.params.values():
            cited |= {v} if isinstance(v, str) else set(v) if isinstance(v, list) else set()
    out.derived = [f for f in out.derived if f.id in cited]
    return out


# --- refresh ------------------------------------------------------------------------------


def cited_claims(section: Section) -> list[str]:
    """The claim ids that a section's text and action basis cite, in reading order."""
    ids: list[str] = []
    for block in section.blocks:
        if isinstance(block, Prose | Action):
            ids += [m.group(2) for m in ANY_CLAIM.finditer(block.text)]
        if isinstance(block, Action):
            ids += block.basis
    return list(dict.fromkeys(ids))


# The params that say what a claim is about. Fact ids and period labels are not here: new
# data may move a value or add a period without changing what a claim is about.
_ABOUT = ("subject", "member", "a_label", "b_label", "where", "metric", "whole", "noun", "nouns",
          "which", "period")


def _identity(claim: Claim) -> tuple:
    """What a claim is about, and for a rank or an extreme the members it compares."""
    about = tuple((k, str(claim.params.get(k))) for k in _ABOUT)
    return about + (tuple(sorted(claim.params.get("population", []))),)


def outdated_reviews(spec: ReportSpec, old: dict[str, Fact], new: dict[str, Fact]) -> ReportSpec:
    """Mark a model's review of a section's words as outdated when a value it read changed.

    Example: a model passed "West held up well: {claim:...}" for a small rise. A refresh makes
    the rise large. The claim is still true, but no model read the words against the new value.
    A check by code (connecting words only, or fixed wording) stays valid."""
    sections = []
    for section in spec.sections:
        review = section.review
        if review is not None and not review.outdated and "model" in (review.wording, review.omissions):
            read = {f for c in cited_claims(section) if c in spec.claims for f in spec.claims[c].facts}
            read |= {f for b in section.blocks if isinstance(b, Prose) for f in FACT_TOKEN.findall(b.text)}
            if any(_value(old.get(f)) != _value(new.get(f)) for f in read):
                section = section.model_copy(update={"review": review.model_copy(update={"outdated": True})})
        sections.append(section)
    return spec.model_copy(update={"sections": sections})


def _value(fact: Fact | None):
    return None if fact is None else (fact.value, fact.state)


def recheck(spec: ReportSpec, fresh: list[Claim], refs: set[str], facts: dict[str, Fact]) -> ReportSpec:
    """Check each cited claim of the queries ``refs`` against new results.

    A cited claim is stale when code no longer derives its text from the facts, or when the
    new candidate with the same id is about something else (another segment, other members).
    A moved value alone is not stale: the claim's words do not name the data's span. A stale claim keeps its words, and
    its section lists it in ``stale_claims``: nothing is rewritten silently. The new
    candidates replace the claims that no text cites."""
    new = {c.id: c for c in fresh}
    stored = spec.claims
    sections, cited_all = [], set()
    for section in spec.sections:
        ids = cited_claims(section)
        cited_all |= set(ids)
        mine = [i for i in ids if i not in stored or stored[i].query_ref in refs]
        stale = [
            i for i in mine
            if i not in stored
            or (i in new and _identity(new[i]) != _identity(stored[i]))
            or not check(stored[i], facts)
        ]
        kept = [i for i in section.stale_claims if i not in mine]
        sections.append(section.model_copy(update={"stale_claims": [*kept, *stale]}))
    stale_all = {i for s in sections for i in s.stale_claims}
    claims = {k: c for k, c in stored.items() if c.query_ref not in refs or k in cited_all}
    claims |= {k: c for k, c in new.items() if k not in stale_all}
    return spec.model_copy(update={"sections": sections, "claims": claims})


def expected_derived(
    spec: ReportSpec, query_defs: dict[str, dict], results: dict[str, Result], facts: dict[str, Fact]
) -> dict[str, Fact]:
    """The derived facts that code computes from ``results``, for the trace check.

    ``facts`` holds the base facts of a fresh run. A query definition in an old shape has no
    derived facts, so it is skipped."""
    intents = {f"q{s.id[1:]}": s.intent for s in spec.sections}
    focus = {f"q{s.id[1:]}": s.focus for s in spec.sections}
    out: dict[str, Fact] = {}
    for ref, definition in query_defs.items():
        if ref not in results:
            continue
        try:
            query = Query.model_validate(definition)
        except ValueError:
            continue
        owned = [f for k, f in facts.items() if k.split(".", 1)[0] in (ref, f"{ref}_total")]
        out |= {f.id: f for f in claims_for(query, ref, results, owned, intents.get(ref, ""),
                                            focus=focus.get(ref, ())).derived}
    return out | cross_facts(spec, facts)


def keep_stale_facts(spec: ReportSpec, facts: dict[str, Fact], old: dict[str, Fact]) -> dict[str, Fact]:
    """``facts`` plus the old facts that a stale claim reads, so its sentence keeps its values
    until the section is rewritten (a grain or segment change removes derived facts)."""
    kept = dict(facts)
    for section in spec.sections:
        for cid in section.stale_claims:
            claim = spec.claims.get(cid)
            if claim is None:
                continue
            ids = set(FACT_TOKEN.findall(claim.text))
            for v in claim.params.values():
                ids |= {v} if isinstance(v, str) else set(v) if isinstance(v, list) else set()
            kept |= {i: old[i] for i in ids if i not in kept and i in old}
    return kept


# --- a comparison across sections -----------------------------------------------------------


def _member_fact(facts: dict[str, Fact], ref: str, metric: str, label: str) -> str | None:
    """The fact of one member of section ``ref`` (by its label), or of its total ("total")."""
    if label.lower() == "total":
        return next((i for i in (f"{ref}_total.{metric}", f"{ref}.{metric}") if i in facts), None)
    prefix = f"{ref}.{metric}."
    for fact in facts.values():
        if not fact.id.startswith(prefix) or fact.binding is None or fact.binding.share:
            continue
        keys = {k: v for k, v in fact.binding.keys.items() if v is not None}
        if keys and " · ".join(value_label(k, v) for k, v in keys.items()) == label:
            return fact.id
    return None


def _scope(definition: dict) -> str:
    """The filter values of a section, as people read them: "Web", or "all" with no filter."""
    query = Query.model_validate(definition)
    return ", ".join(value_label(m, v) for m, v in _segment(query).items()) or "all"


def cross_claim(this: tuple[str, str], earlier: tuple[str, str], metric_label: str,
                facts: dict[str, Fact], query_defs: dict[str, dict]) -> tuple[Claim, Fact] | None:
    """A compare claim between a member of this section and one of an earlier section.

    ``this`` and ``earlier`` are ``(ref, member label or "total")``. Both sections must have the
    same metric and the same period. Each side names its section's filters: "West (Web)".
    ``None`` when a rule fails or a value is missing."""
    (ref, a_label), (old, b_label) = this, earlier
    da, db = query_defs.get(ref), query_defs.get(old)
    if da is None or db is None or ref == old:
        return None
    if (da.get("metrics") or [None])[0] != (db.get("metrics") or [None])[0]:
        return None
    window = lambda d: ((d.get("time") or {}).get("start"), (d.get("time") or {}).get("end"))  # noqa: E731
    if window(da) != window(db) or (da.get("time") or {}).get("grain") or (db.get("time") or {}).get("grain"):
        return None
    metric = da["metrics"][0]
    a, b = _member_fact(facts, ref, metric, a_label), _member_fact(facts, old, metric, b_label)
    if a is None or b is None or not _ok(facts, a, b) or facts[a].value <= 0 or facts[b].value <= 0:
        return None
    sides = [(a, f"{a_label} ({_scope(da)})"), (b, f"{b_label} ({_scope(db)})")]
    (a, a_text), (b, b_text) = sorted(sides, key=lambda s: -facts[s[0]].value)
    ratio = cross_ratio(a, b, facts, f"{ref}.derived.{metric}.cross.{_slug(a_text)}.{_slug(b_text)}",
                        f"{a_text} to {b_text}")
    params = {"a": a, "b": b, "ratio": ratio.id, "a_label": a_text, "b_label": b_text,
              "metric": metric_label[:1].lower() + metric_label[1:], "cross": True}
    text = derive("compare", params, facts | {ratio.id: ratio})
    if text is None:
        return None
    claim = Claim(
        id=f"{ref}.cross.{metric}.{_slug(a_text)}.{_slug(b_text)}", kind="compare", text=text,
        facts=list(dict.fromkeys(FACT_TOKEN.findall(text))), query_ref=ref, params=params,
        evidence=Evidence(rows=[EvidenceRow(label=a_text, fact=a, highlight=True),
                                EvidenceRow(label=b_text, fact=b, highlight=True)]))
    return claim, ratio


def cross_ratio(a: str, b: str, facts: dict[str, Fact], fid: str, label: str) -> Fact:
    """The derived ratio of two facts from two sections, formatted as a compare ratio."""
    r = facts[a].value / facts[b].value
    return facts[a].model_copy(update={
        "id": fid, "label": label, "value": r, "formatted": f"{r:.1f}×" if r >= 1.1 else f"{r:.2f}×",
        "binding": None, "basis": f"{facts[a].label} divided by {facts[b].label}"})


def cross_facts(spec: ReportSpec, facts: dict[str, Fact]) -> dict[str, Fact]:
    """The ratio facts of the report's cross-section claims, derived again from ``facts``."""
    out = {}
    for claim in spec.claims.values():
        p = claim.params
        if p.get("cross") and _ok(facts, p["a"], p["b"]) and facts[p["b"]].value:
            out[p["ratio"]] = cross_ratio(p["a"], p["b"], facts, p["ratio"],
                                          f"{p['a_label']} to {p['b_label']}")
    return out
