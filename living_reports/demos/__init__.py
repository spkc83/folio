"""The demo datasets. ``LR_DEMO`` picks one: ``retail``, ``fraud``, ``hr``, or ``orders``.

Each generated demo (retail, fraud, hr) holds a generator, the semantic model seed, the two
moments of its data, one definition change, and its eval questions. Code generates the data
into the data directory the first time it is needed, so a demo needs no file outside the repo.

``orders`` is the compact orders model that the unit tests use. Its data comes from
``LR_SOURCE_PATH``.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from living_reports.semantic.kinds import FilterSpec

DemoName = Literal["retail", "fraud", "hr", "orders"]
MOMENT_1_END = "2025-12-01"  # moment 1 ends before December 2025


@dataclass(frozen=True)
class Definition:
    """The demo's definition change at moment 2: one metric gets a measure filter."""

    metric: str
    filter: FilterSpec
    definition: str
    title: str
    reason: str


@dataclass(frozen=True)
class Demo:
    name: DemoName
    title: str
    placeholder: str  # the example question in the ask box
    keys: dict[str, tuple[str, ...]]
    seed: Callable[..., None]  # (graph, *, source_id, dialect, schema)
    tables: Callable[[Path, int], dict[str, str]]  # (data location, moment) -> table SQL
    definition: Definition
    questions: Path
    generate: Callable[[Path], None] | None = None  # writes <table>.parquet into a folder
    version: str = "1"  # a new version makes the app generate the data again
    starters: list[str] = field(default_factory=list)


def u(expr: str, salt: str) -> str:
    """SQL for a uniform number in [0, 1) from a hash: the same on every machine and thread
    count, for one DuckDB version."""
    return f"((hash({expr}, '{salt}') % 1000003) / 1000003.0)"


def write_parquet(con, folder: Path, tables: dict[str, str]) -> None:
    """``COPY (SELECT <columns> FROM <table>)`` to ``<folder>/<table>.parquet`` for each table."""
    for table, columns in tables.items():
        target = str(folder / f"{table}.parquet").replace("'", "''")
        con.execute(f"COPY (SELECT {columns} FROM {table}) TO '{target}' (FORMAT PARQUET)")


def parquet(folder: Path, table: str) -> str:
    path = str(folder / f"{table}.parquet").replace("'", "''")
    return f"read_parquet('{path}')"


def moment_tables(folder: Path, moment: int, fact: str, time_field: str, held: str,
                  dims: list[str]) -> dict[str, str]:
    """Moment 1: the fact table before December 2025, without the held column. Moment 2: all."""
    if moment not in (1, 2):
        raise ValueError(f"The demo has no moment {moment}.")
    read = parquet(folder, fact)
    if moment == 1:
        sql = (f'SELECT * EXCLUDE ("{held}") FROM {read} '
               f"WHERE \"{time_field}\" < TIMESTAMP '{MOMENT_1_END}'")
    else:
        sql = f"SELECT * FROM {read}"
    return {fact: sql, **{d: f"SELECT * FROM {parquet(folder, d)}" for d in dims}}


def get_demo(name: DemoName | None = None) -> Demo:
    from importlib import import_module

    from living_reports.config import get_settings

    return import_module(f"{__name__}.{name or get_settings().demo}").DEMO
