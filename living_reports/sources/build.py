"""Build DuckDB source files, and the demo source with its two moments.

A build writes a new file next to the target and moves it into place with ``os.replace``.
A reader that has the old file open keeps the old inode; the next run of a
``DuckDBSource`` attaches the new file. This module holds the only DuckDB connection
with write access.
"""
import os
import threading
from collections.abc import Mapping
from pathlib import Path

import duckdb

from living_reports.config import Settings, get_settings
from living_reports.sources.base import SourceError
from living_reports.store import Store, get_store

DEMO_MOMENTS = (1, 2)
# Moment 1 ends before December 2025 and does not have this column yet.
_MOMENT_1_WHERE = "order_created_date < TIMESTAMP '2025-12-01'"
_HELD_COLUMN = "contract_length_months"

_build_lock = threading.Lock()


class BuildError(SourceError):
    """A source file cannot be built from its data."""


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _check_keys(conn: duckdb.DuckDBPyConnection, keys: Mapping[str, tuple[str, ...]]) -> None:
    for table, key in keys.items():
        columns = ", ".join(_ident(c) for c in key)
        rows, distinct = conn.execute(
            f"SELECT count(*), count(DISTINCT ({columns})) FROM main.{_ident(table)}"
        ).fetchone()
        if rows != distinct:
            names = ", ".join(key)
            raise BuildError(
                f"{table} has more than one row for a {names} "
                f"({rows - distinct} extra rows); {names} must be unique."
            )


def build_duckdb_file(
    target: Path, tables: Mapping[str, str], keys: Mapping[str, tuple[str, ...]] | None = None
) -> Path:
    """Write ``main.<name> AS <select>`` for each table, check the keys, then replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    partial.unlink(missing_ok=True)
    try:
        with duckdb.connect(str(partial)) as conn:
            for name, sql in tables.items():
                conn.execute(f"CREATE TABLE main.{_ident(name)} AS {sql}")
            _check_keys(conn, keys or {})
    except BaseException:
        partial.unlink(missing_ok=True)
        partial.with_name(partial.name + ".wal").unlink(missing_ok=True)
        raise
    os.replace(partial, target)
    return target


def demo_tables(source_path: Path, moment: int) -> dict[str, str]:
    """The demo tables at a moment: ``orders`` and ``dealers`` from the demo Parquet file."""
    if moment not in DEMO_MOMENTS:
        raise ValueError(f"The demo has no moment {moment}.")
    read = f"read_parquet({_literal(str(source_path))})"
    where = f" WHERE {_MOMENT_1_WHERE}" if moment == 1 else ""
    exclude = ", ".join(_ident(c) for c in ("region", *((_HELD_COLUMN,) if moment == 1 else ())))
    return {
        "orders": f"SELECT * EXCLUDE ({exclude}) FROM {read}{where}",
        # A dealer is in the demo at a moment only when it has an order at that moment.
        "dealers": f"SELECT DISTINCT dealer_id, region FROM {read}{where} ORDER BY dealer_id",
    }


def _data_dir(settings: Settings) -> Path:
    return settings.data_dir.expanduser().resolve()


def demo_source_path(settings: Settings | None = None) -> Path:
    return _data_dir(settings or get_settings()) / "sources" / "duckdb_demo.duckdb"


def demo_moment(store: Store) -> int | None:
    row = store.row("SELECT value FROM app_state WHERE key = 'demo_moment'")
    return int(row[0]) if row else None


def _current(target: Path, source: Path, store: Store, moment: int) -> bool:
    return (
        target.exists()
        and target.stat().st_mtime >= source.stat().st_mtime
        and demo_moment(store) == moment
    )


def demo_available(settings: Settings | None = None) -> bool:
    """A generated demo always has data. The orders demo needs LR_SOURCE_PATH."""
    from living_reports.demos import get_demo

    settings = settings or get_settings()
    return get_demo(settings.demo).generate is not None or settings.source_path is not None


def _generated(settings: Settings) -> Path:
    """The folder of the demo's generated Parquet files. Generate them when they are missing
    or from an older generator version. Returns the version file: its time is the data's."""
    from living_reports.demos import get_demo

    demo = get_demo(settings.demo)
    folder = _data_dir(settings) / "demo" / demo.name
    stamp = folder / "VERSION"
    if stamp.exists() and stamp.read_text() == demo.version:
        return stamp
    partial = folder.with_name(f"{demo.name}.{os.getpid()}.tmp")
    partial.mkdir(parents=True, exist_ok=True)
    demo.generate(partial)
    (partial / "VERSION").write_text(demo.version)
    if folder.exists():
        for old in folder.iterdir():
            old.unlink()
        folder.rmdir()
    os.replace(partial, folder)
    return stamp


def build_demo_source(moment: int, settings: Settings | None = None) -> Path:
    """Build the demo source at ``moment`` unless the file is already current. Idempotent."""
    from living_reports.demos import get_demo

    settings = settings or get_settings()
    demo = get_demo(settings.demo)
    if demo.generate is None and settings.source_path is None:
        raise BuildError("LR_SOURCE_PATH is not set; the demo source has no data.")
    target = demo_source_path(settings)
    store = get_store(settings)
    with _build_lock:
        if demo.generate is not None:
            source = _generated(settings)
            location = source.parent
        else:
            source = location = settings.source_path.expanduser()
        if _current(target, source, store, moment):
            return target
        build_duckdb_file(target, demo.tables(location, moment), demo.keys)
        store.execute(
            "INSERT OR REPLACE INTO app_state VALUES ('demo_moment', ?)", [str(moment)]
        )
    return target
