"""Date ranges: the engine end is exclusive, the words show the inclusive last month.

``TimeRange.end`` is the first day after the range. A producer that means "through a day"
sends the next day (``exclusive_end``). A display shows the last month in the range
(``span_text``).
"""
from datetime import date, timedelta

from living_reports.engine.query import exclusive_end, is_month_last_day
from living_reports.semantic.labels import month_text

__all__ = ["exclusive_end", "last_day", "span_text"]  # exclusive_end lives in the engine

DASH = " – "


def last_day(end: date) -> date:
    """The inclusive last day of a range that ends before ``end``.

    An old stored end can be a month's last day (``2025-11-30``) that the producer meant
    as inclusive. Its month is the month of the end itself, not the month before.
    """
    return end if is_month_last_day(end) else end - timedelta(days=1)


def span_text(start: date | None, end: date | None) -> str:
    """The range in words, with the inclusive last month: ``Jan 2024 – Nov 2025``.

    ``2025`` for one calendar year, ``Mar 2025`` for one month. ``end`` is exclusive.
    """
    if start is None and end is None:
        return "All dates"
    if end is None:
        return f"From {month_text(start)}"
    through = last_day(end)
    if start is None:
        return f"Through {month_text(through)}"
    if start == date(start.year, 1, 1) and end == date(start.year + 1, 1, 1):
        return str(start.year)
    if (start.year, start.month) == (through.year, through.month):
        return month_text(start)
    return f"{month_text(start)}{DASH}{month_text(through)}"
