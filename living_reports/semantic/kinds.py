"""Folio's kinds on the graph engine: payload models and edge rules.

Node id conventions:

- Source: the source name (``duckdb_demo``).
- Dataset: the dataset name (``orders``).
- Field: the member id ``dataset.field`` (``orders.region``).
- Relationship: the relationship name (``orders_dealer``).
- MetricBinding: the metric id (``fraud_loss``), a concept id, never a physical name.
- Report: ``report:{report_id}``.
- Edition: ``edition:{report_id}:{number}``.
- Thread: ``thread:{thread_id}``.

Edition and Thread payloads hold the plain ``report_id``; their edges go to ``report:{id}``.

Metrics are typed. No payload holds SQL text except a row-level ``Field.expression`` and
the display text of an ``Expression`` metric.
"""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from folio_graph import EdgeRule, KindSpec

Op = Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "between", "is_null", "contains"]
Scalar = str | int | float | bool
FieldType = Literal["string", "integer", "decimal", "boolean", "datetime", "date"]
Agg = Literal["sum", "avg", "min", "max", "count", "count_distinct"]
Format = Literal["currency", "percent", "count", "number"]

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
# An identifier that may reach SQL: a name, a qualified name, or a member id.
Ident = Annotated[str, StringConstraints(pattern=rf"^{_NAME}$")]
QualifiedName = Annotated[str, StringConstraints(pattern=rf"^{_NAME}(\.{_NAME})*$")]
IDENTIFIER = re.compile(rf"^{_NAME}$")
MemberId = Annotated[str, StringConstraints(pattern=rf"^{_NAME}\.{_NAME}$")]

_ONE_VALUE = {"eq", "ne", "gt", "gte", "lt", "lte", "contains"}


def check_values(op: str, values: list) -> None:
    """Raise ``ValueError`` when ``op`` cannot take this many values."""
    n = len(values)
    ok = (
        (op in _ONE_VALUE and n == 1)
        or (op in ("in", "not_in") and n >= 1)
        or (op == "between" and n == 2)
        or (op == "is_null" and n == 0)
    )
    if not ok:
        raise ValueError(f"Operator {op} cannot take {n} value(s)")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterSpec(_Strict):
    member: MemberId
    op: Op
    values: list[Scalar] = []

    @model_validator(mode="after")
    def _value_count(self) -> "FilterSpec":
        check_values(self.op, self.values)
        return self


class SqlExpression(_Strict):
    """A row-level field expression."""

    sql: str
    dialect: str = "duckdb"


class DimensionSpec(_Strict):
    is_time: bool = False


class SourcePayload(_Strict):
    name: Ident
    dialect: str = "duckdb"
    supports_as_of: bool = False


class DatasetPayload(_Strict):
    name: Ident
    source_id: Ident
    table: QualifiedName
    # Empty when the source declares no key; a relationship then cannot join to it
    # on the primary key (engine/paths.py), only on a declared unique key.
    primary_key: list[Ident] = []
    unique_keys: list[list[Ident]] = []
    time_field: Ident | None = None
    description: str = ""


class FieldPayload(_Strict):
    name: Ident
    dataset: Ident
    column: Ident
    type: FieldType
    label: str | None = None
    dimension: DimensionSpec | None = None
    expression: SqlExpression | None = None
    synonyms: list[str] = []


class RelationshipPayload(_Strict):
    """Many-to-one from ``from_dataset`` to ``to_dataset``."""

    name: Ident
    from_dataset: Ident
    to_dataset: Ident
    from_columns: list[Ident] = Field(min_length=1)
    to_columns: list[Ident]
    role: Ident | None = None

    @model_validator(mode="after")
    def _same_length(self) -> "RelationshipPayload":
        if len(self.from_columns) != len(self.to_columns):
            raise ValueError("from_columns and to_columns must have the same length")
        return self


class Measure(_Strict):
    agg: Agg
    field: MemberId | None = None
    dataset: Ident | None = None
    filter: FilterSpec | None = None

    @model_validator(mode="after")
    def _needs_input(self) -> "Measure":
        if self.agg == "count":
            if self.field is None and self.dataset is None:
                raise ValueError("count needs a dataset or a field")
        elif self.field is None:
            raise ValueError(f"{self.agg} needs a field")
        return self


class Ratio(_Strict):
    numerator: Ident
    denominator: Ident


class Expression(_Strict):
    """An expression metric. ``ast`` is a sqlglot serde dump of ``sql``.

    ``MetricBindingPayload`` checks it with ``engine.expressions.validate_expression``.
    """

    sql: str
    dialect: str
    dataset: Ident
    fields: list[MemberId]
    ast: list | dict


# Aggregates whose value grows with the rows counted. A target cannot apply to them.
ADDITIVE_AGGS = {"sum", "count", "count_distinct"}


class MetricBindingPayload(_Strict):
    name: Ident
    label: str
    format: Format
    definition: str = ""
    synonyms: list[str] = []
    measure: Measure | None = None
    ratio: Ratio | None = None
    expression: Expression | None = None
    certified: bool = True
    # Business knowledge: the better direction ("lower" for fraud loss) and, for a rate or an
    # average, the target value. Claims use them for "a change for the worse" and
    # "misses the target". None: the metric has no better direction.
    better: Literal["higher", "lower"] | None = None
    target: float | None = None

    @model_validator(mode="after")
    def _one_form(self) -> "MetricBindingPayload":
        forms = [f for f in (self.measure, self.ratio, self.expression) if f is not None]
        if len(forms) != 1:
            raise ValueError("A metric needs exactly one of measure, ratio, expression")
        if self.target is not None:
            if self.better is None:
                raise ValueError("A target needs better: higher or lower")
            if self.measure is not None and self.measure.agg in ADDITIVE_AGGS:
                # A total depends on the length of the period, so one target cannot fit it.
                raise ValueError("A target applies only to a rate or an average, not to a sum or a count")
        if self.expression is not None:
            # The query engine owns the AST rules; import it when a payload needs them.
            from living_reports.engine.errors import QueryRejected
            from living_reports.engine.expressions import validate_expression

            try:
                validate_expression(self.expression)
            except QueryRejected as error:
                raise ValueError(error.reason) from error
        return self


class ReportPayload(_Strict):
    title: str
    tier: str
    author: str


class EditionPayload(_Strict):
    report_id: str
    number: int
    known_at: datetime
    graph_version: int
    content_hash: str


class ThreadPayload(_Strict):
    report_id: str
    section_id: str
    fact_id: str | None = None
    status: Literal["open", "closed"] = "open"
    decision: str | None = None


FOLIO_KINDS: tuple[KindSpec, ...] = (
    KindSpec("Source", SourcePayload),
    KindSpec(
        "Dataset",
        DatasetPayload,
        (
            EdgeRule("in_source", "Dataset", "Source", "one"),
            EdgeRule("joins_to", "Dataset", "Dataset", "many"),
        ),
    ),
    KindSpec("Field", FieldPayload, (EdgeRule("belongs_to", "Field", "Dataset", "one"),)),
    KindSpec(
        "Relationship", RelationshipPayload, (EdgeRule("relates", "Relationship", "Dataset"),)
    ),
    KindSpec(
        "MetricBinding",
        MetricBindingPayload,
        (
            EdgeRule("measures", "MetricBinding", "Field"),
            EdgeRule("measures", "MetricBinding", "Dataset"),
            EdgeRule("uses", "MetricBinding", "MetricBinding"),
        ),
    ),
    KindSpec(
        "Report",
        ReportPayload,
        (
            EdgeRule("uses", "Report", "MetricBinding"),
            EdgeRule("uses", "Report", "Field"),
        ),
    ),
    KindSpec(
        "Edition",
        EditionPayload,
        (
            EdgeRule("built_from", "Edition", "Report", "one"),
            EdgeRule("cites", "Edition", "MetricBinding"),
        ),
    ),
    KindSpec("Thread", ThreadPayload, (EdgeRule("discussed_in", "Thread", "Report", "one"),)),
)
