"""The schema of one source for the home page: the terms a person can ask about, and the columns.

Only modeled columns are shown. A column outside the model is not something Folio can answer.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from living_reports.api.deps import Stores, stores
from living_reports.semantic.model import Metric, SemanticModel, Term
from living_reports.semantic.projections import vocabulary_of
from living_reports.sources import SourceError
from living_reports.sources.base import ColumnInfo, TableInfo
from living_reports.sources.registry import source_for, sources

router = APIRouter(prefix="/api")


class SchemaColumn(BaseModel):
    field_id: str
    name: str
    label: str
    type: str
    dimension: bool
    is_time: bool
    synonyms: list[str] = []
    distinct_count: int | None = None
    null_rate: float | None = None
    samples: list[str] = []
    min: str | None = None
    max: str | None = None


class SchemaDataset(BaseModel):
    name: str
    table: str
    row_count: int | None = None
    primary_key: list[str] = []
    columns: list[SchemaColumn]


class SourceSchema(BaseModel):
    source_id: str
    metrics: list[Term]
    dimensions: list[Term]
    datasets: list[SchemaDataset]
    profiled: bool


def _metric_datasets(model: SemanticModel, metric: Metric, seen: frozenset = frozenset()) -> set[str]:
    """The datasets that a metric reads, through its ratio parts too."""
    if metric.id in seen:
        return set()
    out: set[str] = set()
    if metric.measure is not None:
        if metric.measure.dataset:
            out.add(metric.measure.dataset)
        for ref in (metric.measure.field, metric.measure.filter.member if metric.measure.filter else None):
            if ref:
                out.add(ref.split(".", 1)[0])
    if metric.expression is not None:
        out.add(metric.expression.dataset)
    if metric.ratio is not None:
        for part in (metric.ratio.numerator, metric.ratio.denominator):
            try:
                out |= _metric_datasets(model, model.metric(part), seen | {metric.id})
            except KeyError:
                continue
    return out


def _tables(source_id: str) -> tuple[dict[str, TableInfo], bool]:
    """The source's tables by qualified name; empty when the source cannot profile now."""
    try:
        catalog = source_for(source_id).introspect()
    except SourceError:
        return {}, False
    return {t.table: t for t in catalog.tables}, True


@router.get("/sources/{source_id}/schema", response_model=SourceSchema)
def source_schema(source_id: str, s: Stores = Depends(stores)) -> SourceSchema:
    if source_id not in sources():
        raise HTTPException(status_code=404, detail=f"No source {source_id}")
    model = s.graph.model()
    mine = [d for d in model.datasets if d.source_id == source_id]
    names = {d.name for d in mine}
    tables, profiled = _tables(source_id)
    datasets = []
    for d in mine:
        table = tables.get(d.table)
        info: dict[str, ColumnInfo] = {c.name: c for c in table.columns} if table else {}
        columns = []
        for f in d.fields:
            c = info.get(f.column)
            columns.append(SchemaColumn(
                field_id=f.id, name=f.column, label=f.label, type=f.type,
                dimension=f.dimension is not None,
                is_time=bool(f.dimension and f.dimension.is_time),
                synonyms=f.synonyms,
                **({} if c is None else {
                    "distinct_count": c.distinct_count, "null_rate": c.null_rate,
                    "samples": c.samples, "min": c.min, "max": c.max,
                }),
            ))
        datasets.append(SchemaDataset(
            name=d.name, table=d.table, row_count=table.row_count if table else None,
            primary_key=d.primary_key, columns=columns,
        ))
    vocab = vocabulary_of(model)
    metrics = [t for t in vocab.metrics
               if (ds := _metric_datasets(model, model.metric(t.id))) and ds <= names]
    dimensions = [t for t in vocab.dimensions if t.dataset in names]
    return SourceSchema(source_id=source_id, metrics=metrics, dimensions=dimensions,
                        datasets=datasets, profiled=profiled)
