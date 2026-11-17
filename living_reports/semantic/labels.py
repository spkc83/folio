"""Values in the words people read.

``display_value`` gives the label of one value of a member: ``bust_out`` is ``Bust-out``,
``none`` of the fraud type is ``No fraud``, the month ``2024-01-01`` is ``Jan 2024``. Fact
labels, chart labels, chips, and the data brief use it. Ids, filters, and stored keys keep
the raw value. The web mirror is ``web/src/report/labels.ts``: keep the two the same.
"""
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

NO_VALUE = "No value"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

# Labels that the general rule cannot make, by field name (the member id after the dot).
# A value label from the semantic model wins over these.
VALUE_LABELS: dict[str, dict[str, str]] = {
    "fraud_type": {"none": "No fraud", "bust_out": "Bust-out"},
}

_CODE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]00:00(?::00(?:\.0+)?)?)?$")


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE.match(value):
        return date.fromisoformat(value[:10])
    return None


def month_text(day: date) -> str:
    """``Jan 2024``."""
    return f"{MONTHS[day.month - 1]} {day.year}"


def _day_text(day: date) -> str:
    return f"{day.day} {month_text(day)}"


def _date_text(day: date, grain: str | None) -> str:
    if grain == "year":
        return str(day.year)
    if grain == "quarter":
        return f"Q{(day.month - 1) // 3 + 1} {day.year}"
    if grain == "week":
        return f"Week of {_day_text(day)}"
    if grain == "day" or (grain is None and day.day != 1):
        return _day_text(day)
    return month_text(day)


def display_value(member: str, value: Any, labels: Mapping[str, str] | None = None) -> str:
    """The label of ``value``, one value of ``member``.

    ``member`` is a member id (``orders.fraud_type``), a time column
    (``orders.order_created_date:month``), or ``month`` (the cube's month key). ``labels``
    maps a raw value to the semantic model's label for it; a label there wins.

    >>> display_value("orders.fraud_type", "dealer_collusion")
    'Dealer collusion'
    >>> display_value("orders.order_created_date:month", "2024-01-01")
    'Jan 2024'
    """
    if value is None:
        return NO_VALUE
    raw = str(value)
    if labels and raw in labels:
        return labels[raw]
    column, _, grain = member.partition(":")
    name = column.rsplit(".", 1)[-1]
    if raw in VALUE_LABELS.get(name, {}):
        return VALUE_LABELS[name][raw]
    day = _as_date(value)
    if day is not None:
        return _date_text(day, grain or ("month" if name == "month" else None))
    if isinstance(value, str) and _CODE.match(value):
        return value.replace("_", " ").capitalize()
    return raw
