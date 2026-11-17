"""The ``Source`` protocol and the catalog types (spec 6.1).

Every read of analytical data goes through a ``Source``. A run is read-only, one
statement, with a row cap and a timeout, and it returns an Arrow table.
"""
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import pyarrow as pa
from pydantic import BaseModel

from living_reports.semantic.kinds import FieldType

DEFAULT_LIMIT = 1000
DEFAULT_TIMEOUT_S = 20.0
TRUNCATED = b"truncated"
DATA_TIME = b"data_time"


class SourceError(Exception):
    """A source cannot answer. The text never holds a path, a DSN, or a password."""


class SourceTimeout(SourceError):
    def __init__(self, source_id: str, timeout_s: float):
        super().__init__(f"{source_id} did not answer within {timeout_s:g} s")
        self.source_id = source_id
        self.timeout_s = timeout_s


class ForeignKey(BaseModel):
    columns: list[str]
    to_table: str
    to_columns: list[str]


class ColumnInfo(BaseModel):
    name: str
    source_type: str
    type: FieldType | None
    nullable: bool = True
    distinct_count: int | None = None
    null_rate: float | None = None
    samples: list[str] = []
    min: str | None = None
    max: str | None = None


class TableInfo(BaseModel):
    schema_name: str
    name: str
    columns: list[ColumnInfo]
    row_count: int | None = None
    primary_key: list[str] = []
    unique_keys: list[list[str]] = []
    foreign_keys: list[ForeignKey] = []

    @property
    def table(self) -> str:
        return f"{self.schema_name}.{self.name}"

    def column(self, name: str) -> ColumnInfo:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


class Catalog(BaseModel):
    source_id: str
    dialect: str
    taken_at: datetime
    tables: list[TableInfo]

    def table(self, qualified: str) -> TableInfo:
        for t in self.tables:
            if t.table == qualified:
                return t
        raise KeyError(qualified)

    @property
    def members(self) -> list[str]:
        return sorted(f"{t.name}.{c.name}" for t in self.tables for c in t.columns)


@runtime_checkable
class Source(Protocol):
    id: str
    dialect: str
    supports_as_of: bool

    def introspect(self) -> Catalog: ...

    def run(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        limit: int = DEFAULT_LIMIT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        as_of: datetime | None = None,
    ) -> pa.Table: ...

    def data_time(self) -> datetime: ...

    def check(self) -> None: ...


def truncated(table: pa.Table) -> bool:
    """True when the statement produced more rows than the cap."""
    return (table.schema.metadata or {}).get(TRUNCATED) == b"1"


def with_data_time(table: pa.Table, data_time: datetime) -> pa.Table:
    """``table`` stamped with the data time of the data that the run read."""
    meta = {**(table.schema.metadata or {}), DATA_TIME: data_time.isoformat().encode()}
    return table.replace_schema_metadata(meta)


def data_time_of(table: pa.Table) -> datetime | None:
    """The data time that the source stamped on a run result, or None."""
    raw = (table.schema.metadata or {}).get(DATA_TIME)
    return datetime.fromisoformat(raw.decode()) if raw else None


def capped(table: pa.Table, limit: int) -> pa.Table:
    """``table`` cut to ``limit`` rows, flagged when rows were cut."""
    if table.num_rows <= limit:
        return table
    table = table.slice(0, limit)
    return table.replace_schema_metadata({**(table.schema.metadata or {}), TRUNCATED: b"1"})


def refuse_as_of(source: Any, as_of: datetime | None) -> None:
    if as_of is not None and not source.supports_as_of:
        raise SourceError(f"{source.id} does not support as-of reads")
