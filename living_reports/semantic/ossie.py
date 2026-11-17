"""Apache Ossie (0.2.0.dev0) export and import of the semantic model.

Export writes the confirmed model as an Ossie document. Folio's typed metric (its measure,
ratio, or expression; its label, format, and certification) rides in a ``folio`` entry of
``custom_extensions``, so another tool reads plain SQL and Folio reads its own form back.

Import turns a document into proposals, never into confirmed nodes. A document comes from
outside, so every part of it is checked: names through the kinds validators, a computed
field through the expression node allowlist, a SQL-only metric through ``parse_expression``.
An imported expression metric is always uncertified, whatever the document says; an owner
certifies it in a separate step.
"""
import json

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlglot import exp
from sqlglot.errors import SqlglotError

from folio_graph import GraphError, NodeChange
from living_reports.discovery.preview import model_from_changes
from living_reports.discovery.propose import Dropped
from living_reports.discovery.run import DiscoveryRun
from living_reports.engine.compile import measure_sql
from living_reports.engine.errors import QueryRejected
from living_reports.engine.expressions import DIALECTS, _check_node, parse_expression
from living_reports.semantic.kinds import (
    IDENTIFIER,
    DatasetPayload,
    DimensionSpec,
    Expression,
    FieldPayload,
    Measure,
    MetricBindingPayload,
    Ratio,
    RelationshipPayload,
    SourcePayload,
    SqlExpression,
)
from living_reports.semantic.model import Metric, SemanticModel, default_label
from living_reports.sources.base import Catalog, TableInfo

OSSIE_VERSION = "0.2.0.dev0"
FOLIO_VENDOR = "folio"
MAX_DOCUMENT_BYTES = 1_000_000
# An Ossie document nests about eight levels (dataset, field, expression, dialects, ...).
MAX_DEPTH = 32

DATATYPE_OUT = {
    "string": "String",
    "integer": "Integer",
    "decimal": "Decimal",
    "boolean": "Boolean",
    "datetime": "DateTime",
    "date": "Date",
}
DATATYPE_IN = {v: k for k, v in DATATYPE_OUT.items()} | {"Float": "decimal", "DateTimeTz": "datetime"}
_TIME_TYPES = ("datetime", "date")


# The document ----------------------------------------------------------------------


class _Ossie(BaseModel):
    # Unknown keys stay, so a document from a newer draft round-trips what Folio ignores.
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class OssieDialect(_Ossie):
    dialect: str
    expression: str


class OssieExpression(_Ossie):
    sql: str | None = None
    dialects: list[OssieDialect] = []

    @model_validator(mode="after")
    def _one(self) -> "OssieExpression":
        if self.sql is None and not self.dialects:
            raise ValueError("An expression needs sql or a dialects entry")
        return self


class OssieDimension(_Ossie):
    is_time: bool | None = None


class OssieAiContext(_Ossie):
    instructions: str | None = None
    synonyms: list[str] = []
    examples: list[str] = []


class OssieExtension(_Ossie):
    vendor_name: str
    data: str


class OssieField(_Ossie):
    name: str
    expression: OssieExpression
    dimension: OssieDimension | None = None
    label: str | None = None
    description: str | None = None
    datatype: str | None = None
    ai_context: OssieAiContext | str | None = None
    custom_extensions: list[OssieExtension] = []


class OssieRelationship(_Ossie):
    name: str
    from_: str = Field(alias="from")
    to: str
    from_columns: list[str]
    to_columns: list[str]
    ai_context: OssieAiContext | str | None = None
    custom_extensions: list[OssieExtension] = []


class OssieMetric(_Ossie):
    name: str
    expression: OssieExpression
    description: str | None = None
    datatype: str | None = None
    ai_context: OssieAiContext | str | None = None
    custom_extensions: list[OssieExtension] = []


class OssieDataset(_Ossie):
    name: str
    source: str
    primary_key: list[str] = []
    unique_keys: list[list[str]] = []
    description: str | None = None
    fields: list[OssieField] = []
    metrics: list[OssieMetric] = []
    ai_context: OssieAiContext | str | None = None
    custom_extensions: list[OssieExtension] = []


class OssieDocument(_Ossie):
    version: str
    name: str
    description: str | None = None
    datasets: list[OssieDataset] = Field(min_length=1)
    relationships: list[OssieRelationship] = []
    metrics: list[OssieMetric] = []
    ai_context: OssieAiContext | str | None = None
    custom_extensions: list[OssieExtension] = []


# Export ----------------------------------------------------------------------------


def _ext(data: dict) -> list[OssieExtension]:
    return [OssieExtension(vendor_name=FOLIO_VENDOR, data=json.dumps(data))]


def _ai(synonyms: list[str]) -> OssieAiContext | None:
    return OssieAiContext(synonyms=synonyms) if synonyms else None


def _dialects(model: SemanticModel, sql: str) -> OssieExpression:
    return OssieExpression(dialects=[OssieDialect(dialect=model.dialect.upper(), expression=sql)])


def metric_sql(metric: Metric, model: SemanticModel) -> str:
    """A metric as one SQL expression on member ids (a definition, so filter values are literals)."""
    if metric.measure is not None:
        return measure_sql(metric.measure, model, model.dialect)
    if metric.ratio is not None:
        num = metric_sql(model.metric(metric.ratio.numerator), model)
        den = metric_sql(model.metric(metric.ratio.denominator), model)
        return f"{num} / NULLIF({den}, 0)"
    return metric.expression.sql


def _export_metric(metric: Metric, model: SemanticModel) -> OssieMetric:
    form = {
        key: getattr(metric, key).model_dump(mode="json")
        for key in ("measure", "ratio", "expression")
        if getattr(metric, key) is not None
    }
    data = {"label": metric.label, "format": metric.format, "certified": metric.certified, **form}
    # The business fields go out only when set, so a metric without them exports as before.
    data |= {k: v for k in ("better", "target") if (v := getattr(metric, k)) is not None}
    return OssieMetric(
        name=metric.id,
        expression=_dialects(model, metric_sql(metric, model)),
        description=metric.definition or None,
        ai_context=_ai(metric.synonyms),
        custom_extensions=_ext(data),
    )


def export_model(model: SemanticModel) -> str:
    """The model as Ossie YAML."""
    datasets = []
    for d in model.datasets:
        fields = []
        for f in d.fields:
            if f.expression is None:
                expression = OssieExpression(sql=f.column)
            else:
                expression = OssieExpression(dialects=[OssieDialect(
                    dialect=f.expression.dialect.upper(), expression=f.expression.sql)])
            extensions = []
            if f.dimension is None and f.type in _TIME_TYPES:
                # Ossie reads a date field with no dimension key as a time dimension.
                extensions = _ext({"dimension": None})
            fields.append(OssieField(
                name=f.name,
                expression=expression,
                datatype=DATATYPE_OUT[f.type],
                label=f.label,
                dimension=None if f.dimension is None else OssieDimension(is_time=f.dimension.is_time),
                ai_context=_ai(f.synonyms),
                custom_extensions=extensions,
            ))
        datasets.append(OssieDataset(
            name=d.name, source=d.table, primary_key=d.primary_key, unique_keys=d.unique_keys,
            fields=fields,
            custom_extensions=_ext({"time_field": d.time_field}) if d.time_field else [],
        ))
    relationships = [
        OssieRelationship(
            name=r.name, from_=r.from_dataset, to=r.to_dataset, from_columns=r.from_columns,
            to_columns=r.to_columns, custom_extensions=_ext({"role": r.role}) if r.role else [],
        )
        for r in model.relationships
    ]
    doc = OssieDocument(
        version=OSSIE_VERSION,
        name=model.name,
        datasets=datasets,
        relationships=relationships,
        metrics=[_export_metric(m, model) for m in model.metrics],
    )
    body = doc.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


# Import ----------------------------------------------------------------------------


class ImportResult(BaseModel):
    run: DiscoveryRun
    dropped: list[Dropped]


def _scan(text: str) -> None:
    """Refuse aliases and deep nesting before the document is built.

    Aliases can expand a small file into a huge tree, and deep nesting exhausts the
    recursion of the loader; an Ossie file never needs either. The event parser is not
    recursive, so this scan is safe on any input.
    """
    depth = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent):
            raise ValueError("YAML anchors and aliases are not supported in a model file.")
        if isinstance(event, yaml.CollectionStartEvent):
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError("The document is nested too deeply.")
        elif isinstance(event, yaml.CollectionEndEvent):
            depth -= 1


def parse_document(text: str) -> OssieDocument:
    """Read an Ossie document (YAML or JSON). Raises ``ValueError`` with a sentence."""
    if len(text.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError("The document is larger than 1 MB.")
    try:
        _scan(text)
        raw = yaml.safe_load(text)
    except RecursionError:
        raise ValueError("The document is nested too deeply.") from None
    except yaml.YAMLError as error:
        raise ValueError(f"The document is not valid YAML: {error}") from None
    if not isinstance(raw, dict):
        raise ValueError("An Ossie document is a mapping with version, name, and datasets.")
    version = str(raw.get("version", ""))
    if not version.startswith("0.2"):
        raise ValueError(f"Ossie version {version or '(none)'} is not supported; 0.2 is.")
    try:
        return OssieDocument.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ValueError(f"The document is not valid Ossie: {where}: {first['msg']}") from None


def _folio(extensions: list[OssieExtension]) -> dict:
    for e in extensions:
        if e.vendor_name == FOLIO_VENDOR:
            try:
                data = json.loads(e.data)
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def _synonyms(ai: OssieAiContext | str | None) -> list[str]:
    return list(ai.synonyms) if isinstance(ai, OssieAiContext) else []


def _first_sql(e: OssieExpression) -> tuple[str, str]:
    """(sql, dialect) of an expression: ``sql`` first, else the first dialects entry."""
    if e.sql is not None:
        return e.sql, "duckdb"
    first = e.dialects[0]
    return first.expression, first.dialect.lower()


def _error(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(p) for p in first["loc"])
    return f"{where}: {first['msg']}" if where else first["msg"]


def check_field_expression(sql: str, dialect: str) -> None:
    """A computed field is one row-level expression over its own columns.

    No aggregate, no subquery, no qualified column, and only the node classes that an
    expression metric may hold. Raises ``QueryRejected`` with a sentence.
    """
    if dialect not in DIALECTS:
        raise QueryRejected(f"The dialect {dialect} is not supported; use one of: {', '.join(DIALECTS)}.")
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except SqlglotError as error:
        raise QueryRejected(f"The expression does not parse: {error}") from None
    if len(statements) != 1 or statements[0] is None:
        raise QueryRejected("A computed field is one expression.")
    tree = statements[0]
    for node in tree.walk():
        if isinstance(node, exp.AggFunc):
            raise QueryRejected("A computed field is a row expression; it cannot aggregate.")
        if isinstance(node, exp.Subquery | exp.Select):
            raise QueryRejected("A computed field cannot contain a subquery.")
        if isinstance(node, exp.Column) and (node.table or node.args.get("db")):
            raise QueryRejected(f"{node.sql()} must name a column of its own dataset, unqualified.")
        _check_node(node)


def _table_of(source: str, catalog: Catalog) -> TableInfo | None:
    for t in catalog.tables:
        if t.table == source:
            return t
    named = [t for t in catalog.tables if t.name == source]
    return named[0] if len(named) == 1 else None


def _qualify(sql: str, dataset: str, dialect: str) -> str:
    """A dataset-scoped metric's text with unqualified columns qualified by the dataset."""
    tree = sqlglot.parse_one(sql, read=dialect)
    for col in tree.find_all(exp.Column):
        if not col.table:
            col.set("table", exp.to_identifier(dataset))
    return tree.sql(dialect=dialect)


def _expression_dataset(sql: str, dialect: str, model: SemanticModel) -> str:
    """The one dataset an expression's member ids name. Raises ``QueryRejected``."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError as error:
        raise QueryRejected(f"The expression does not parse: {error}") from None
    named = sorted({c.table for c in tree.find_all(exp.Column) if c.table})
    if not named:
        raise QueryRejected("The expression names no dataset; use member ids (orders.loss_amount).")
    known = [d for d in named if d in {x.name for x in model.datasets}]
    return (known or named)[0]


def _metric_payload(
    m: OssieMetric, scope: str | None, model: SemanticModel
) -> MetricBindingPayload:
    """Raise ``QueryRejected`` or ``ValidationError`` when the metric cannot be imported."""
    data = _folio(m.custom_extensions)
    base = {
        "name": m.name,
        "label": data.get("label") or default_label(m.name),
        "format": data.get("format") or "number",
        "definition": m.description or "",
        "synonyms": _synonyms(m.ai_context),
        "better": data.get("better"),
        "target": data.get("target"),
    }
    if data.get("measure") is not None:
        return MetricBindingPayload(**base, measure=Measure.model_validate(data["measure"]),
                                    certified=bool(data.get("certified", True)))
    if data.get("ratio") is not None:
        return MetricBindingPayload(**base, ratio=Ratio.model_validate(data["ratio"]),
                                    certified=bool(data.get("certified", True)))
    # SQL: from the Ossie expression, never from a stored AST. Always uncertified.
    sql, dialect = _first_sql(m.expression)
    if dialect not in DIALECTS:
        raise QueryRejected(f"The dialect {dialect} is not supported; use one of: {', '.join(DIALECTS)}.")
    if scope is not None:
        try:
            sql = _qualify(sql, scope, dialect)
        except SqlglotError as error:
            raise QueryRejected(f"The expression does not parse: {error}") from None
    dataset = scope or _expression_dataset(sql, dialect, model)
    expression: Expression = parse_expression(sql, dataset, model, dialect)
    return MetricBindingPayload(**base, expression=expression, certified=False)


def document_to_changes(
    doc: OssieDocument, source: SourcePayload, catalog: Catalog | None = None
) -> tuple[list[NodeChange], list[Dropped]]:
    """Proposed changes in confirmation order, and the parts dropped with their reasons."""
    dropped: list[Dropped] = []

    def drop(name: str, kind: str, reason: str) -> None:
        dropped.append(Dropped(name=name, kind=kind, reason=reason))

    taken: dict[str, str] = {source.name: "Source"}
    tables_used: dict[str, str] = {}
    columns_of: dict[str, set[str]] = {}
    datasets: list[NodeChange] = []
    fields: list[NodeChange] = []
    for d in doc.datasets:
        table_name = d.source
        columns: dict[str, str] | None = None
        if catalog is not None:
            table = _table_of(d.source, catalog)
            if table is None:
                drop(d.name, "Dataset", f"{d.source} is not a table of {source.name}")
                continue
            table_name = table.table
            columns = {c.name: c.type for c in table.columns if c.type is not None}
        if d.name in taken:
            drop(d.name, "Dataset", f"name collides with {taken[d.name]}")
            continue
        if table_name in tables_used:
            drop(d.name, "Dataset", f"{table_name} is already the table of {tables_used[table_name]}")
            continue
        time_field = None
        ds_fields: list[NodeChange] = []
        for f in d.fields:
            member = f"{d.name}.{f.name}"
            payload, reason = _field_payload(f, d.name, columns)
            if payload is None:
                drop(member, "Field", reason)
                continue
            if payload.dimension is not None and payload.dimension.is_time and time_field is None:
                time_field = payload.name
            ds_fields.append(NodeChange(id=member, kind="Field", payload=payload.model_dump(mode="json")))
        wanted = _folio(d.custom_extensions).get("time_field")
        times = {c.payload["column"] for c in ds_fields
                 if (c.payload.get("dimension") or {}).get("is_time")}
        if wanted in times:
            time_field = wanted
        try:
            ds = DatasetPayload(
                name=d.name, source_id=source.name, table=table_name, primary_key=d.primary_key,
                unique_keys=d.unique_keys, time_field=time_field, description=d.description or "",
            )
        except ValidationError as error:
            drop(d.name, "Dataset", _error(error))
            continue
        taken[d.name] = "Dataset"
        tables_used[table_name] = d.name
        columns_of[d.name] = {c.payload["column"] for c in ds_fields}
        datasets.append(NodeChange(id=d.name, kind="Dataset", payload=ds.model_dump(mode="json")))
        for c in ds_fields:
            taken[c.id] = "Field"
        fields.extend(ds_fields)

    names = {c.id for c in datasets}
    relationships: list[NodeChange] = []
    for r in doc.relationships:
        missing = [x for x in (r.from_, r.to) if x not in names]
        if missing:
            drop(r.name, "Relationship", f"unknown dataset {missing[0]}")
            continue
        if r.name in taken:
            drop(r.name, "Relationship", f"name collides with {taken[r.name]}")
            continue
        bad = [f"{ds}.{c}" for ds, cols in ((r.from_, r.from_columns), (r.to, r.to_columns))
               for c in cols if c not in columns_of[ds]]
        if bad:
            drop(r.name, "Relationship", f"unknown column {bad[0]}")
            continue
        try:
            payload = RelationshipPayload(
                name=r.name, from_dataset=r.from_, to_dataset=r.to, from_columns=r.from_columns,
                to_columns=r.to_columns, role=_folio(r.custom_extensions).get("role"),
            )
        except ValidationError as error:
            drop(r.name, "Relationship", _error(error))
            continue
        taken[r.name] = "Relationship"
        relationships.append(
            NodeChange(id=r.name, kind="Relationship", payload=payload.model_dump(mode="json"))
        )

    head = [NodeChange(id=source.name, kind="Source", payload=source.model_dump(mode="json")),
            *datasets, *fields, *relationships]
    try:
        model = model_from_changes(head)
    except GraphError as error:
        raise ValueError(str(error)) from None
    scoped = [(m, None) for m in doc.metrics] + [(m, d.name) for d in doc.datasets
                                                  if d.name in names for m in d.metrics]
    metrics: list[NodeChange] = []
    for m, scope in scoped:
        if m.name in taken:
            drop(m.name, "MetricBinding", f"name collides with {taken[m.name]}")
            continue
        try:
            payload = _metric_payload(m, scope, model)
        except QueryRejected as error:
            drop(m.name, "MetricBinding", error.reason)
            continue
        except ValidationError as error:
            drop(m.name, "MetricBinding", _error(error))
            continue
        taken[m.name] = "MetricBinding"
        metrics.append(
            NodeChange(id=m.name, kind="MetricBinding", payload=payload.model_dump(mode="json"))
        )
    return [*head, *metrics], dropped


def _field_payload(
    f: OssieField, dataset: str, columns: dict[str, str] | None
) -> tuple[FieldPayload | None, str]:
    """The payload of one Ossie field, or ``None`` and the reason it is dropped."""
    if not IDENTIFIER.match(f.name):
        return None, f"the name {f.name!r} is not an identifier"
    sql, dialect = _first_sql(f.expression)
    plain = IDENTIFIER.match(sql.strip()) is not None
    if f.datatype is not None:
        ftype = DATATYPE_IN.get(f.datatype)
        if ftype is None:
            return None, f"unsupported datatype {f.datatype}"
    elif columns is not None and plain and sql.strip() in columns:
        ftype = columns[sql.strip()]
    else:
        return None, "no datatype"
    expression = None
    column = f.name
    if plain:
        column = sql.strip()
        if columns is not None and column not in columns:
            return None, f"unknown column {dataset}.{column}"
    else:
        try:
            check_field_expression(sql, dialect)
        except QueryRejected as error:
            return None, error.reason
        expression = SqlExpression(sql=sql, dialect=dialect)
    folio = _folio(f.custom_extensions)
    if f.dimension is not None:
        dimension = DimensionSpec(is_time=bool(f.dimension.is_time))
    elif "dimension" in folio:
        dimension = None
    elif ftype in _TIME_TYPES:
        dimension = DimensionSpec(is_time=True)  # the Ossie default for date types
    else:
        dimension = None
    try:
        payload = FieldPayload(
            name=f.name, dataset=dataset, column=column, type=ftype,
            label=f.label or default_label(f.name), dimension=dimension,
            expression=expression, synonyms=_synonyms(f.ai_context),
        )
    except ValidationError as error:
        return None, _error(error)
    return payload, ""
