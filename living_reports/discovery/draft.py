"""The discovery draft: what the model proposes, and a JSON schema bounded by the catalog.

The model sees the catalog (names, types, counts, a few samples), never the data. Every
name it can write is an enum of catalog members, so a constrained decoder cannot invent
one. The enums are capped, so a wide catalog still gives a small grammar.
"""
from pydantic import BaseModel, ConfigDict, Field, model_validator

from living_reports.discovery.candidates import RelationshipCandidate
from living_reports.harness.llm import bound_schema, escape_data, inline_refs
from living_reports.semantic.kinds import IDENTIFIER, Agg, FilterSpec, Format
from living_reports.sources.base import Catalog, ColumnInfo, TableInfo

MAX_DATASETS = 20
MAX_RELATIONSHIPS = 20
MAX_METRICS = 12
MAX_MEMBERS = 400
# The dimension columns that the model may list for one dataset.
MAX_DIMENSIONS = 20
MAX_SAMPLES = 3
SAMPLE_CHARS = 40
MAX_TEXT = 200
TIME_TYPES = ("datetime", "date")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftDataset(_Strict):
    name: str
    time_field: str | None = None
    dimensions: list[str] = []


class DraftRelationship(_Strict):
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]
    confidence: float = Field(ge=0, le=1)


class DraftMetric(_Strict):
    """The flat form of a typed measure (plan ruling 2)."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str
    format: Format
    definition: str = ""
    synonyms: list[str] = []
    agg: Agg
    field: str | None = None
    dataset: str | None = None
    filter: FilterSpec | None = None

    @model_validator(mode="after")
    def _needs_input(self) -> "DraftMetric":
        if self.agg == "count":
            if self.field is None and self.dataset is None:
                raise ValueError("count needs a dataset or a field")
        elif self.field is None:
            raise ValueError(f"{self.agg} needs a field")
        return self


class DiscoveryDraft(_Strict):
    datasets: list[DraftDataset] = Field(min_length=1, max_length=MAX_DATASETS)
    relationships: list[DraftRelationship] = Field(default=[], max_length=MAX_RELATIONSHIPS)
    metrics: list[DraftMetric] = Field(default=[], max_length=MAX_METRICS)


# The catalog as the model may see it --------------------------------------------------


def mappable(column: ColumnInfo) -> bool:
    return column.type is not None and IDENTIFIER.match(column.name) is not None


def unmappable_reason(column: ColumnInfo) -> str:
    if column.type is None:
        return f"unsupported type {column.source_type}"
    return f"the name {column.name!r} is not an identifier"


def dataset_tables(catalog: Catalog) -> dict[str, TableInfo]:
    """Dataset name -> table, for tables with an identifier name and a mappable column.

    The first table of a name wins (two schemas can hold the same table name).
    """
    out: dict[str, TableInfo] = {}
    for table in catalog.tables:
        if IDENTIFIER.match(table.name) and table.name not in out:
            if any(mappable(c) for c in table.columns):
                out[table.name] = table
    return out


def visible(catalog: Catalog) -> dict[str, list[ColumnInfo]]:
    """The tables and columns that the model sees, capped for the grammar and the prompt."""
    tables = list(dataset_tables(catalog).items())[:MAX_DATASETS]
    per_table = max(5, MAX_MEMBERS // max(1, len(tables)))
    out: dict[str, list[ColumnInfo]] = {}
    room = MAX_MEMBERS
    for name, table in tables:
        cols = [c for c in table.columns if mappable(c)][: min(per_table, room)]
        room -= len(cols)
        out[name] = cols
    return out


def _set_enum(node: dict, values: list[str]) -> dict:
    """Restrict a string (or a nullable string) node to ``values``."""
    if "anyOf" in node:
        branches = [b for b in node["anyOf"] if b.get("type") != "string"]
        if values:
            branches.insert(0, {"type": "string", "enum": values})
        rest = {k: v for k, v in node.items() if k != "anyOf"}
        return {**rest, "anyOf": branches} if len(branches) > 1 else {**rest, **branches[0]}
    node = {k: v for k, v in node.items() if k != "pattern"}
    return {**node, "enum": values}


def draft_schema(catalog: Catalog) -> dict:
    """``DiscoveryDraft``'s JSON schema with every name an enum of catalog members."""
    seen = visible(catalog)
    datasets = sorted(seen)
    members = [f"{d}.{c.name}" for d in datasets for c in seen[d]]
    times = [f"{d}.{c.name}" for d in datasets for c in seen[d] if c.type in TIME_TYPES]
    columns = sorted({c.name for cols in seen.values() for c in cols})
    s = inline_refs(DiscoveryDraft.model_json_schema())
    props = s["properties"]

    ds = props["datasets"]["items"]["properties"]
    ds["name"] = _set_enum(ds["name"], datasets)
    ds["time_field"] = _set_enum(ds["time_field"], times)
    ds["dimensions"]["items"] = _set_enum(ds["dimensions"]["items"], members)
    ds["dimensions"]["maxItems"] = MAX_DIMENSIONS

    rel = props["relationships"]["items"]["properties"]
    for key in ("from_dataset", "to_dataset"):
        rel[key] = _set_enum(rel[key], datasets)
    for key in ("from_columns", "to_columns"):
        rel[key]["items"] = _set_enum(rel[key]["items"], columns)
        rel[key]["maxItems"] = 4
    rel["name"]["maxLength"] = 64

    met = props["metrics"]["items"]["properties"]
    met["field"] = _set_enum(met["field"], members)
    met["dataset"] = _set_enum(met["dataset"], datasets)
    met["synonyms"]["maxItems"] = 4
    met["synonyms"]["items"]["maxLength"] = 40
    met["definition"]["maxLength"] = 200
    met["label"]["maxLength"] = 80
    flt = next(b for b in met["filter"]["anyOf"] if b.get("type") == "object")
    flt["properties"]["member"] = _set_enum(flt["properties"]["member"], members)
    flt["properties"]["values"]["maxItems"] = 8
    for branch in flt["properties"]["values"]["items"].get("anyOf", []):
        if branch.get("type") == "string":
            branch["maxLength"] = 80
    met["name"]["maxLength"] = 64
    # Every key is required (null where allowed): a model leaves out an optional key, and a
    # metric with neither field nor dataset fails validation.
    for node in (s, props["datasets"]["items"], props["relationships"]["items"],
                 props["metrics"]["items"], flt):
        node["required"] = list(node["properties"])
    return bound_schema(s, max_items=MAX_DATASETS, max_length=MAX_TEXT)


def _pct(rate: float | None) -> str:
    return "?" if rate is None else f"{rate:.0%}"


def _sample(values: list[str]) -> str:
    return ", ".join(escape_data(v[:SAMPLE_CHARS]).replace("\n", " ") for v in values[:MAX_SAMPLES])


def catalog_text(catalog: Catalog, candidates: list[RelationshipCandidate]) -> str:
    """The catalog as prompt lines: tables, mappable columns with statistics, candidates."""
    tables = dataset_tables(catalog)
    lines = []
    for name, cols in sorted(visible(catalog).items()):
        rows = tables[name].row_count
        lines.append(f"- {name} ({'?' if rows is None else rows} rows)")
        for c in cols:
            distinct = "?" if c.distinct_count is None else c.distinct_count
            line = f"  - {name}.{c.name}: {c.type}, {distinct} distinct, {_pct(c.null_rate)} null"
            if c.samples:
                line += f", e.g. {_sample(c.samples)}"
            lines.append(line)
    if candidates:
        lines.append("candidates:")
        for c in candidates[:MAX_RELATIONSHIPS]:
            lines.append(
                f"- {c.from_dataset}.{','.join(c.from_columns)} -> "
                f"{c.to_dataset}.{','.join(c.to_columns)} ({c.origin}, {c.confidence})"
            )
    return "\n".join(lines)
