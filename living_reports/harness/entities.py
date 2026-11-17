"""Entities that code finds in a question, without a model.

Four kinds: a value of a small dimension that the question names, one explicit period, a start
date ("after March 2025", "since 2024"), and a count of items ("top 3"). The router uses them only to fill gaps in the model's query.
"""
import re
from dataclasses import dataclass, field
from datetime import date

# Values that are also common words. They never count as a named value.
_STOP = {"none", "other", "unknown", "all", "any", "yes", "no", "true", "false", "n/a", "na"}
_MONTHS = {
    name: number
    for number, name in enumerate(
        ("january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"),
        start=1,
    )
}
_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10}
_COUNT = r"(\d{1,3}|" + "|".join(_NUMBERS) + ")"
_LIMITS = (
    re.compile(rf"\btop\s+{_COUNT}\b", re.IGNORECASE),
    re.compile(
        rf"\b{_COUNT}\s+(?:largest|biggest|highest|lowest|smallest|most|least|worst|best)\b",
        re.IGNORECASE,
    ),
)
_MONTH_YEAR = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+((?:19|20)\d{2})\b", re.IGNORECASE)
_QUARTER = re.compile(r"\bQ([1-4])\s+((?:19|20)\d{2})\b", re.IGNORECASE)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_RELATIVE = re.compile(r"\b(last|this)\s+(month|quarter|year)\b", re.IGNORECASE)
_SINCE = re.compile(r"\b(after|since)\s+(?:the\s+end\s+of\s+)?$", re.IGNORECASE)


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # the first day after the period
    text: str  # the words in the question


@dataclass
class Found:
    values: dict[str, list[str]] = field(default_factory=dict)
    period: Period | None = None
    limit: int | None = None
    # "after March 2025" is 2025-04-01; "since March 2025" is 2025-03-01. Not a period.
    since: date | None = None


def _forms(value: str) -> list[str]:
    forms = [value]
    if "_" in value:
        forms.append(value.replace("_", " "))
    return forms


def _named(value: str, question: str) -> bool:
    """A whole-word match. A value with a capital letter must match its case ("App", not "app"),
    so that a common word ("new") is not read as a proper value ("New")."""
    if value.lower() in _STOP or value.isdigit() or len(value) < 2:
        return False
    proper = any(c.isupper() for c in value)
    flags = 0 if proper else re.IGNORECASE
    for form in _forms(value):
        for m in re.finditer(rf"(?<![\w+]){re.escape(form)}(?![\w+])", question, flags):
            # A capital at the start of a sentence says nothing: "New orders" is not "New".
            if not proper or not _sentence_start(question, m.start()):
                return True
    return False


def _sentence_start(text: str, at: int) -> bool:
    before = text[:at].rstrip(" \"'(")
    return not before or before[-1] in ".?!"


def _month_after(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _relative(unit: str, which: str, last: date) -> tuple[date, date]:
    """``last`` is the data's last day. "Last" means the last complete period in the data.

    "This" means the calendar period that holds ``last``, to its end: "this year" with data to
    30 Nov 2025 is 2025-01-01 to 2026-01-01. An end at the data's last month would keep a
    certified question from counting the December rows that arrive later."""
    end_of_data = date.fromordinal(last.toordinal() + 1)
    if which == "this":
        if unit == "month":
            return last.replace(day=1), _month_after(last)
        if unit == "quarter":
            start = _quarter_start(last)
            return start, _month_after(_month_after(_month_after(start)))
        return date(last.year, 1, 1), date(last.year + 1, 1, 1)
    if unit == "month":
        end = end_of_data if end_of_data.day == 1 else last.replace(day=1)
        return date(end.year - (end.month == 1), (end.month - 2) % 12 + 1, 1), end
    if unit == "quarter":
        end = _quarter_start(end_of_data) if end_of_data == _quarter_start(end_of_data) else _quarter_start(last)
        start_month = end.month - 3
        return date(end.year - (start_month < 1), (start_month - 1) % 12 + 1, 1), end
    end = date(last.year + 1, 1, 1) if end_of_data == date(last.year + 1, 1, 1) else date(last.year, 1, 1)
    return date(end.year - 1, 1, 1), end


def _periods(question: str, last: date) -> list[Period]:
    found: list[Period] = []
    rest = question
    for m in _MONTH_YEAR.finditer(question):
        start = date(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
        found.append(Period(start, _month_after(start), m.group(0)))
        rest = rest.replace(m.group(0), " ")
    for m in _QUARTER.finditer(question):
        start = date(int(m.group(2)), 3 * int(m.group(1)) - 2, 1)
        found.append(Period(start, _month_after(_month_after(_month_after(start))), m.group(0)))
        rest = rest.replace(m.group(0), " ")
    for m in _RELATIVE.finditer(question):
        start, end = _relative(m.group(2).lower(), m.group(1).lower(), last)
        found.append(Period(start, end, m.group(0)))
    for m in _YEAR.finditer(rest):
        year = int(m.group(1))
        found.append(Period(date(year, 1, 1), date(year + 1, 1, 1), m.group(0)))
    return found


def _limit(question: str) -> int | None:
    for pattern in _LIMITS:
        m = pattern.search(question)
        if m:
            word = m.group(1).lower()
            count = _NUMBERS.get(word) or int(word)
            return count if 1 <= count <= 1000 else None
    return None


def find(question: str, values: dict[str, list[str]], first: date, last: date) -> Found:
    """``values`` maps a member id to its values. ``first`` and ``last`` are the data's dates."""
    named = {m: [v for v in sorted(vs, key=str) if _named(str(v), question)] for m, vs in values.items()}
    periods = _periods(question, last)
    period = periods[0] if len(periods) == 1 else None
    if period is not None and (period.end <= first or period.start > last):
        period = None
    since = None
    if period is not None:
        word = _SINCE.search(question[: question.find(period.text)])
        if word is not None:
            since = period.end if word.group(1).lower() == "after" else period.start
            period = None
    return Found(values={m: v for m, v in named.items() if v}, period=period, limit=_limit(question),
                 since=since)
