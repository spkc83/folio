"""The query form (spec 5.1): metrics, member dimensions, filters, a time range, order, limit."""

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from living_reports.semantic.kinds import FilterSpec, Ident, MemberId, Op, Scalar, check_values

Filter = FilterSpec
Grain = Literal["day", "week", "month", "quarter", "year"]
Direction = Literal["asc", "desc"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterGroup(_Strict):
    """``{"and": [...]}`` or ``{"or": [...]}``. Groups nest."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    all: list["Filter | FilterGroup"] | None = Field(default=None, alias="and")
    any: list["Filter | FilterGroup"] | None = Field(default=None, alias="or")

    @model_validator(mode="after")
    def _one_branch(self) -> "FilterGroup":
        branches = [b for b in (self.all, self.any) if b is not None]
        if len(branches) != 1 or not branches[0]:
            raise ValueError("A filter group needs exactly one non-empty 'and' or 'or' list")
        return self

    @property
    def items(self) -> list["Filter | FilterGroup"]:
        return self.all if self.all is not None else self.any


class Having(_Strict):
    """A filter on a metric value, applied after aggregation."""

    metric: Ident
    op: Op
    values: list[Scalar] = []

    @model_validator(mode="after")
    def _value_count(self) -> "Having":
        check_values(self.op, self.values)
        return self


class TimeRange(_Strict):
    """``end`` is exclusive. ``field`` defaults to the base dataset's time field."""

    field: MemberId | None = None
    start: date | None = None
    end: date | None = None
    grain: Grain | None = None

    @model_validator(mode="after")
    def _rules(self) -> "TimeRange":
        if self.start is None and self.end is None and self.grain is None:
            raise ValueError("A time range needs a start, an end, or a grain")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("The time range start must be before its end")
        return self


class Query(_Strict):
    metrics: list[Ident] = Field(min_length=1)
    dimensions: list[MemberId] = []
    filters: list[Filter | FilterGroup] = []
    having: list[Having] = []
    time: TimeRange | None = None
    order: list[tuple[str, Direction]] = []
    limit: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("time", mode="before")
    @classmethod
    def _no_empty_time(cls, value):
        """A time object with no start, end, or grain asks for nothing: read it as none.

        A small model often writes ``{"field": null, "start": null, ...}`` or a lone field.
        """
        if isinstance(value, dict) and not any(value.get(k) for k in ("start", "end", "grain")):
            return None
        return value

    @model_validator(mode="after")
    def _rules(self) -> "Query":
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("A dimension appears more than once")
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("A metric appears more than once")
        outside = [h.metric for h in self.having if h.metric not in self.metrics]
        if outside:
            raise ValueError(f"having names metrics not in the query: {', '.join(outside)}")
        keys = {*self.metrics, *self.dimensions}
        grain = self.time.grain if self.time else None
        for key, _ in self.order:
            is_time = grain is not None and (
                key == self.time_column or (self.time.field is None and key.endswith(f":{grain}"))
            )
            if key not in keys and not is_time:
                raise ValueError(f"order key {key} is not a metric or dimension of the query")
        return self

    @property
    def is_time_series(self) -> bool:
        return self.time is not None and self.time.grain is not None

    @property
    def time_column(self) -> str | None:
        """``{field}:{grain}``; ``None`` without a grain or before the field is resolved."""
        if not self.is_time_series or self.time.field is None:
            return None
        return f"{self.time.field}:{self.time.grain}"


FilterGroup.model_rebuild()
Query.model_rebuild()


def is_time_column(column: str) -> bool:
    """A result column named ``{field}:{grain}`` holds the truncated time."""
    return ":" in column


def flat_filters(items: list[Filter | FilterGroup]) -> list[Filter]:
    """Every filter in ``items``, groups opened."""
    out: list[Filter] = []
    for item in items:
        out += flat_filters(item.items) if isinstance(item, FilterGroup) else [item]
    return out


def totals_of(query: Query) -> Query:
    """The same query with no breakdown: no dimensions, no grain, no having, order, or limit."""
    time = query.time
    if time is not None and time.grain is not None:
        time = time.model_copy(update={"grain": None}) if time.start or time.end else None
    return query.model_copy(
        update={"dimensions": [], "time": time, "having": [], "order": [], "limit": None}
    )


def upgrade_definition(definition: dict, model) -> dict:
    """A stored query in the form before the query engine, rewritten in today's form.

    Reports saved before milestone 4 hold ``{"dimensions": ["region"], "top": 3, ...}``.
    Bare dimension names become member ids; ``month`` becomes a monthly grain.
    """
    old = {"start", "end", "top"} & definition.keys()
    old_filters = any("dimension" in f for f in definition.get("filters", []))
    old_dims = any("." not in d for d in definition.get("dimensions", []))
    if not (old or old_filters or old_dims):
        return definition
    by_name = {}
    for ds in model.datasets:
        for f in ds.fields:
            if f.dimension is not None:
                by_name.setdefault(f.name, f.id)

    def member(name: str) -> str:
        return name if "." in name else by_name.get(name, name)

    out = {"metrics": definition["metrics"]}
    dims = [d for d in definition.get("dimensions", []) if d != "month"]
    out["dimensions"] = [member(d) for d in dims]
    out["filters"] = [
        {
            "member": member(f["dimension"]),
            "op": "in" if isinstance(f["value"], list) and f["op"] == "eq" else f["op"],
            "values": f["value"] if isinstance(f["value"], list) else [f["value"]],
        }
        if "dimension" in f
        else f
        for f in definition.get("filters", [])
    ]
    time = {k: definition[k] for k in ("start", "end") if definition.get(k)}
    if "month" in definition.get("dimensions", []):
        time["grain"] = "month"
    if time:
        out["time"] = time
    if definition.get("top"):
        out["limit"] = definition["top"]
    return out


def is_month_last_day(day: date) -> bool:
    return day.day != 1 and (day + timedelta(days=1)).day == 1


def exclusive_end(end: date | None, last: date | None = None) -> date | None:
    """The exclusive end for an ``end`` that a person or a model wrote.

    ``last`` is the data's last day. An end on the data's last day, or on the last day of a
    month, means "through that day", so the result is the next day. Any other end is
    already exclusive: ``2025-01-01`` stays ``2025-01-01``.
    """
    if end is None:
        return None
    if end == last or is_month_last_day(end):
        return end + timedelta(days=1)
    return end


def stored_query(definition: dict, model) -> Query:
    """A stored query definition as a ``Query``. An older stored form is upgraded first.

    Older reports stored a month's last day (``2025-11-30``) as the end. The engine end is
    exclusive, so that end lost its last day; it reruns as the next day.
    """
    query = Query.model_validate(upgrade_definition(definition, model))
    t = query.time
    if t is None or exclusive_end(t.end) == t.end:
        return query
    return query.model_copy(update={"time": t.model_copy(update={"end": exclusive_end(t.end)})})
