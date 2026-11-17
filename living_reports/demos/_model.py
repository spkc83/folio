"""Write a demo's semantic model into the graph from plain declarations."""
from dataclasses import dataclass, field
from datetime import datetime

from living_reports.semantic.graph import FolioGraph
from living_reports.semantic.kinds import (
    DatasetPayload,
    DimensionSpec,
    FieldPayload,
    FieldType,
    MetricBindingPayload,
    RelationshipPayload,
    SourcePayload,
)

START = datetime(2024, 1, 1)


@dataclass(frozen=True)
class F:
    """A field: its type, and its label when it is a dimension (``time`` for the time field)."""

    type: FieldType
    label: str | None = None
    time: bool = False
    synonyms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class D:
    """A dataset: its key, its time field, and its fields."""

    key: str
    fields: dict[str, F]
    time_field: str | None = None


@dataclass(frozen=True)
class R:
    """A many-to-one relationship on one column with the same name on both sides."""

    from_dataset: str
    to_dataset: str
    column: str


def seed_model(fg: FolioGraph, datasets: dict[str, D], relationships: list[R],
               metrics: list[MetricBindingPayload], *, source_id: str, dialect: str = "duckdb",
               schema: str = "main") -> None:
    """Write the model on ``source_id`` once. A second call changes nothing."""
    if fg.slice().node(source_id) is not None:
        return

    def put(node_id: str, kind: str, payload) -> None:
        fg.put(node_id, kind, payload, reason="demo seed", valid_from=START)

    with fg.backend.transaction():
        put(source_id, "Source", SourcePayload(name=source_id, dialect=dialect))
        for name, d in datasets.items():
            put(name, "Dataset", DatasetPayload(
                name=name, source_id=source_id, table=f"{schema}.{name}", primary_key=[d.key],
                time_field=d.time_field))
            for column, f in d.fields.items():
                dim = DimensionSpec(is_time=True) if f.time else (
                    DimensionSpec() if f.label else None)
                put(f"{name}.{column}", "Field", FieldPayload(
                    name=column, dataset=name, column=column, type=f.type, label=f.label,
                    dimension=dim, synonyms=f.synonyms))
        for r in relationships:
            rel = f"{r.from_dataset}_{r.to_dataset}"
            put(rel, "Relationship", RelationshipPayload(
                name=rel, from_dataset=r.from_dataset, to_dataset=r.to_dataset,
                from_columns=[r.column], to_columns=[r.column]))
        for m in metrics:
            put(m.name, "MetricBinding", m)
