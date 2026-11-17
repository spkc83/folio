"""Arrow helpers: one value shape for every source, and rows for the JSON payloads.

A result holds at most ``limit`` rows of aggregates, so ``rows_of`` is not a scan of
source data (plan ruling 2).
"""
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc


def _normal(column: pa.ChunkedArray) -> pa.ChunkedArray:
    t = column.type
    if pa.types.is_decimal(t):
        return pc.cast(column, pa.float64())
    if pa.types.is_timestamp(t):
        return pc.cast(pc.cast(column, pa.date32()), pa.string())
    if pa.types.is_date(t):
        return pc.cast(column, pa.string())
    return column


def normalize(table: pa.Table) -> pa.Table:
    """Decimals as float64, timestamps as their date, dates as ISO text."""
    columns = [_normal(c) for c in table.columns]
    return pa.Table.from_arrays(columns, names=table.column_names).replace_schema_metadata(
        table.schema.metadata
    )


def columns_of(table: pa.Table) -> list[str]:
    return list(table.column_names)


def rows_of(table: pa.Table) -> list[list[Any]]:
    return [list(r) for r in zip(*(c.to_pylist() for c in table.columns))]
