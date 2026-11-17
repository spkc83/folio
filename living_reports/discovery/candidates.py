"""Relationship candidates from a catalog: declared foreign keys, then name matches.

A name match is a hint for the model and for a person, never a fact. Every relationship
that reaches the graph passes a uniqueness probe on the real data first.
"""
from typing import Literal

from pydantic import BaseModel

from living_reports.sources.base import Catalog, ColumnInfo, TableInfo

DECLARED = 1.0
NAME_MATCH = 0.6


class RelationshipCandidate(BaseModel):
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]
    confidence: float
    origin: Literal["declared", "name"]


def _unique(table: TableInfo, column: ColumnInfo) -> bool:
    return (
        column.distinct_count is not None
        and table.row_count is not None
        and column.distinct_count == table.row_count
    )


def _targets(catalog: Catalog) -> tuple[dict[str, list[TableInfo]], dict[str, list[TableInfo]]]:
    """Indexes built once, so a match is a lookup and the work grows with the columns.

    ``same``: column name -> tables whose primary key is that column or that have a unique
    column of that name. ``ident``: ``<table>_id`` (singular or plural) -> tables with a
    unique ``id``.
    """
    same: dict[str, list[TableInfo]] = {}
    ident: dict[str, list[TableInfo]] = {}
    for t in catalog.tables:
        names = set(t.primary_key[:1]) if len(t.primary_key) == 1 else set()
        names |= {c.name for c in t.columns if _unique(t, c)}
        for name in names:
            same.setdefault(name, []).append(t)
        id_col = next((c for c in t.columns if c.name == "id"), None)
        if id_col is not None and (t.primary_key == ["id"] or _unique(t, id_col)):
            for stem in {t.name, t.name.removesuffix("s")}:
                ident.setdefault(f"{stem}_id", []).append(t)
    return same, ident


def relationship_candidates(catalog: Catalog) -> list[RelationshipCandidate]:
    """Declared keys first (confidence 1.0), then name matches (0.6), by name within each."""
    names = {t.table: t.name for t in catalog.tables}
    found: dict[str, RelationshipCandidate] = {}
    declared: set[tuple[str, str, tuple[str, ...]]] = set()

    def add(c: RelationshipCandidate) -> None:
        name = c.name if c.name not in found else f"{c.name}_{'_'.join(c.from_columns)}"
        found[name] = c.model_copy(update={"name": name})

    for table in catalog.tables:
        for fk in table.foreign_keys:
            to = names.get(fk.to_table)
            if to is None:
                continue
            declared.add((table.name, to, tuple(fk.columns)))
            add(RelationshipCandidate(
                name=f"{table.name}_{to}", from_dataset=table.name, to_dataset=to,
                from_columns=list(fk.columns), to_columns=list(fk.to_columns),
                confidence=DECLARED, origin="declared",
            ))
    same, ident = _targets(catalog)
    for table in catalog.tables:
        for column in table.columns:
            if column.name == "id":
                continue  # a table's own id names that table, not another one
            matches = [(t, [column.name]) for t in same.get(column.name, [])]
            seen = {id(t) for t, _ in matches}
            matches += [(t, ["id"]) for t in ident.get(column.name, []) if id(t) not in seen]
            for target, to_columns in matches:
                if target is table or (table.name, target.name, (column.name,)) in declared:
                    continue
                add(RelationshipCandidate(
                    name=f"{table.name}_{target.name}", from_dataset=table.name,
                    to_dataset=target.name, from_columns=[column.name], to_columns=to_columns,
                    confidence=NAME_MATCH, origin="name",
                ))
    return sorted(found.values(), key=lambda c: (-c.confidence, c.name))
