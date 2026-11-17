"""What the reader pointed at, applied to the routed query in code (not in the prompt)."""
from datetime import date

from pydantic import BaseModel, ConfigDict, model_validator

from living_reports.engine import Query, TimeRange
from living_reports.engine.query import Filter, FilterGroup, stored_query
from living_reports.reports.store import StoredReport
from living_reports.semantic.model import SemanticModel

MONTH = "month"


class Selection(BaseModel):
    """Filters, an inclusive month range (``YYYY-MM-01``), a section, or a fact."""

    model_config = ConfigDict(extra="forbid")

    filters: list[Filter] = []
    range: tuple[date, date] | None = None
    section_id: str | None = None
    fact_id: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "Selection":
        if self.range is not None and self.range[0] > self.range[1]:
            raise ValueError("The first month of a range must not be after the last month.")
        return self


def _month_after(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def _section_query(report: StoredReport, section_id: str, model: SemanticModel | None) -> Query | None:
    definition = report.query_defs.get("q" + section_id[1:])
    if definition is None:
        return None
    return stored_query(definition, model) if model is not None else Query.model_validate(definition)


def with_selection(
    query: Query,
    selection: Selection | None,
    report: StoredReport | None,
    model: SemanticModel | None = None,
) -> Query:
    """The routed query narrowed by the selection.

    Filters apply in this order, and a later filter on a member replaces an earlier one: the
    selected section's filters, the routed query's own, the selected fact's member keys, and
    the selected filters. A selected range (or the fact's month) sets the dates.
    """
    if selection is None:
        return query
    flat: dict[str, Filter] = {}
    groups: list[FilterGroup] = []
    time = query.time
    month: date | None = None

    def add(items) -> None:
        for item in items:
            if isinstance(item, FilterGroup):
                groups.append(item)
            else:
                flat.pop(item.member, None)
                flat[item.member] = item

    if report is not None and selection.section_id:
        section = _section_query(report, selection.section_id, model)
        if section is not None:
            add(section.filters)
            time = time or section.time
    add(query.filters)
    fact = report.facts.get(selection.fact_id) if report and selection.fact_id else None
    if fact is not None and fact.binding is not None:
        for member, value in fact.binding.keys.items():
            if member == MONTH:
                month = date.fromisoformat(value) if value else None
            elif value is None:
                add([Filter(member=member, op="is_null", values=[])])
            else:
                add([Filter(member=member, op="eq", values=[value])])
    add(selection.filters)
    first, last = selection.range or ((month, month) if month else (None, None))
    if first is not None:
        time = TimeRange(
            field=time.field if time else None,
            start=first,
            end=_month_after(last),
            grain=time.grain if time else None,
        )
    return query.model_copy(update={"filters": [*flat.values(), *groups], "time": time})
