"""Typed documents that the graph projects into: the ``SemanticModel`` and the ``Vocabulary``.

The query engine reads the model. The UI and the language model read the vocabulary.
Neither reads raw graph nodes.
"""

import hashlib
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

from living_reports.semantic.kinds import (
    DimensionSpec,
    Expression,
    FieldType,
    Format,
    Measure,
    MetricBindingPayload,
    Op,
    Ratio,
    SqlExpression,
)

GRAINS = ("day", "week", "month", "quarter", "year")
OPS = tuple(get_args(Op))


class _Doc(BaseModel):
    model_config = ConfigDict(frozen=True)


class Field(_Doc):
    id: str
    name: str
    dataset: str
    column: str
    type: FieldType
    label: str
    dimension: DimensionSpec | None = None
    expression: SqlExpression | None = None
    synonyms: list[str] = []
    version: int


class Dataset(_Doc):
    name: str
    source_id: str
    table: str
    primary_key: list[str]
    unique_keys: list[list[str]] = []
    time_field: str | None = None
    fields: list[Field] = []
    version: int


class Relationship(_Doc):
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]
    role: str | None = None
    version: int


class Metric(_Doc):
    id: str
    label: str
    format: Format
    definition: str = ""
    synonyms: list[str] = []
    measure: Measure | None = None
    ratio: Ratio | None = None
    expression: Expression | None = None
    certified: bool = True
    better: Literal["higher", "lower"] | None = None
    target: float | None = None
    version: int

    @classmethod
    def from_payload(cls, metric_id: str, payload: MetricBindingPayload, version: int) -> "Metric":
        data = payload.model_dump(exclude={"name"})
        return cls(id=metric_id, version=version, **data)


def default_label(name: str) -> str:
    return name.replace("_", " ").capitalize()


class SemanticModel(_Doc):
    name: str
    model_version: int
    valid_at: datetime
    known_at: datetime
    source: str | None = None
    dialect: str = "duckdb"
    datasets: list[Dataset] = []
    relationships: list[Relationship] = []
    metrics: list[Metric] = []

    def dataset(self, name: str) -> Dataset:
        for d in self.datasets:
            if d.name == name:
                return d
        raise KeyError(name)

    def field(self, member_id: str) -> Field:
        return self.members[member_id]

    def metric(self, metric_id: str) -> Metric:
        for m in self.metrics:
            if m.id == metric_id:
                return m
        raise KeyError(metric_id)

    @property
    def metric_versions(self) -> dict[str, int]:
        return {m.id: m.version for m in self.metrics}

    @property
    def content_hash(self) -> str:
        """A hash of the definitions only. Writes of reports or threads do not change it."""
        body = self.model_dump_json(exclude={"model_version", "valid_at", "known_at"})
        return hashlib.sha256(body.encode()).hexdigest()

    @property
    def members(self) -> dict[str, Field]:
        return {f.id: f for d in self.datasets for f in d.fields}

    def with_metric(
        self, metric_id: str, payload: MetricBindingPayload, version: int | None = None
    ) -> "SemanticModel":
        """A copy with ``metric_id`` replaced or added. The graph is not changed."""
        current = self.metric_versions.get(metric_id, 0)
        metric = Metric.from_payload(metric_id, payload, version or current + 1)
        others = [m for m in self.metrics if m.id != metric_id]
        metrics = sorted([*others, metric], key=lambda m: m.id)
        return self.model_copy(update={"metrics": metrics})


class Term(_Doc):
    id: str
    label: str
    definition: str = ""
    synonyms: list[str] = []
    format: Format | None = None
    dataset: str | None = None
    is_time: bool = False


class Vocabulary(_Doc):
    metrics: list[Term] = []
    dimensions: list[Term] = []
    time_fields: list[str] = []
    operators: list[str] = list(OPS)
    grains: list[str] = list(GRAINS)
    model_version: int

    def label(self, member_or_metric_id: str) -> str:
        for term in (*self.metrics, *self.dimensions):
            if term.id == member_or_metric_id:
                return term.label
        return member_or_metric_id

    def is_time(self, member_id: str) -> bool:
        return member_id in self.time_fields
