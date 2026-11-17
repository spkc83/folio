"""Projections: read a graph slice into a typed document.

Consumers read these documents, never raw nodes.
"""

from folio_graph import GraphError, GraphSlice
from living_reports.semantic.kinds import (
    DatasetPayload,
    FieldPayload,
    MetricBindingPayload,
    RelationshipPayload,
    SourcePayload,
)
from living_reports.semantic.model import (
    Dataset,
    Field,
    Metric,
    Relationship,
    SemanticModel,
    Term,
    Vocabulary,
    default_label,
)


def _missing_error(what: str, refs: list[str], s: GraphSlice) -> GraphError:
    return GraphError(
        f"{what} refers to {', '.join(refs)}, which is not in the model as of {s.known_at}"
    )


def _metric_refs(p: MetricBindingPayload) -> tuple[list[str], list[str], list[str]]:
    """(field ids, dataset names, metric ids) that a metric names."""
    fields: list[str] = []
    datasets: list[str] = []
    metrics: list[str] = []
    if p.measure is not None:
        fields += [p.measure.field] if p.measure.field else []
        datasets += [p.measure.dataset] if p.measure.dataset else []
        fields += [p.measure.filter.member] if p.measure.filter else []
    if p.expression is not None:
        fields += p.expression.fields
        datasets.append(p.expression.dataset)
    if p.ratio is not None:
        metrics += [p.ratio.numerator, p.ratio.denominator]
    return fields, datasets, metrics


def semantic_model(s: GraphSlice) -> SemanticModel:
    """The datasets, fields, relationships, and metrics confirmed in the slice."""
    sources = {n.id: (SourcePayload(**n.payload), n) for n in s.nodes("Source")}
    ds_nodes = {n.id: (DatasetPayload(**n.payload), n) for n in s.nodes("Dataset")}
    fields_by_ds: dict[str, list[Field]] = {name: [] for name in ds_nodes}
    for n in s.nodes("Field"):
        p = FieldPayload(**n.payload)
        if p.dataset not in fields_by_ds:
            raise _missing_error(f"Field {n.id}", [p.dataset], s)
        fields_by_ds[p.dataset].append(
            Field(
                id=n.id,
                version=n.version,
                label=p.label or default_label(p.name),
                **p.model_dump(exclude={"label"}),
            )
        )
    datasets = []
    for name, (p, n) in ds_nodes.items():
        if p.source_id not in sources:
            raise _missing_error(f"Dataset {name}", [p.source_id], s)
        datasets.append(
            Dataset(
                version=n.version,
                fields=fields_by_ds[name],
                **p.model_dump(exclude={"description"}),
            )
        )
    relationships = []
    for n in s.nodes("Relationship"):
        p = RelationshipPayload(**n.payload)
        missing = [d for d in (p.from_dataset, p.to_dataset) if d not in ds_nodes]
        if missing:
            raise _missing_error(f"Relationship {n.id}", missing, s)
        relationships.append(Relationship(version=n.version, **p.model_dump()))
    members = {f.id for fs in fields_by_ds.values() for f in fs}
    binding_nodes = s.nodes("MetricBinding")
    metric_ids = {n.id for n in binding_nodes}
    metrics = []
    for n in binding_nodes:
        p = MetricBindingPayload(**n.payload)
        fields, dsets, parts = _metric_refs(p)
        missing = (
            [f for f in fields if f not in members]
            + [d for d in dsets if d not in ds_nodes]
            + [m for m in parts if m not in metric_ids]
        )
        if missing:
            raise _missing_error(f"Metric {n.id}", list(dict.fromkeys(missing)), s)
        metrics.append(Metric.from_payload(n.id, p, n.version))
    used = sorted({d.source_id for d in datasets})
    source = used[0] if len(used) == 1 else None
    source_payload = sources[source][0] if source else None
    return SemanticModel(
        name=source_payload.name if source_payload else "",
        model_version=s.version,
        valid_at=s.valid_at,
        known_at=s.known_at,
        source=source,
        dialect=source_payload.dialect if source_payload else "duckdb",
        datasets=datasets,
        relationships=relationships,
        metrics=metrics,
    )


def vocabulary_of(m: SemanticModel) -> Vocabulary:
    metrics = [
        Term(id=x.id, label=x.label, definition=x.definition, synonyms=x.synonyms, format=x.format)
        for x in m.metrics
    ]
    dims = [f for d in m.datasets for f in d.fields if f.dimension is not None]
    dimensions = [
        Term(
            id=f.id,
            label=f.label,
            synonyms=f.synonyms,
            dataset=f.dataset,
            is_time=f.dimension.is_time,
        )
        for f in dims
    ]
    return Vocabulary(
        metrics=metrics,
        dimensions=dimensions,
        time_fields=[t.id for t in dimensions if t.is_time],
        model_version=m.model_version,
    )


def vocabulary(s: GraphSlice) -> Vocabulary:
    """The terms people and the language model use: metrics, dimensions, operators, grains."""
    return vocabulary_of(semantic_model(s))

