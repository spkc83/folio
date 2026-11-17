"""``DuckDBSource``: a DuckDB database file, read-only, with no file or network access.

The file is attached read-only to a fresh in-memory DuckDB instance, and external access
is then switched off and the configuration locked. A query cannot write, attach another
file, read a file, or load an extension.

A build replaces the file with ``os.replace``. The source checks the file's inode and
modification time on each use and attaches the new file when either changed. A run that
started on the old file ends on the old file. (DuckDB caches database instances by path,
so a second ``duckdb.connect(path)`` while the first is alive returns the old data. The
in-memory instance and ``ATTACH`` avoid that cache.)
"""
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from sqlglot import exp

from living_reports.engine.check import parse_select
from living_reports.engine.errors import QueryRejected
from living_reports.sources.base import (
    DEFAULT_LIMIT,
    DEFAULT_TIMEOUT_S,
    Catalog,
    ColumnInfo,
    ForeignKey,
    SourceError,
    SourceTimeout,
    TableInfo,
    capped,
    refuse_as_of,
    with_data_time,
)
from living_reports.sources.types import field_type

_DB = "src"  # the catalog name of the attached file
SAMPLES = 3
SAMPLE_ROWS = 1000


def _ident(name: str) -> str:
    return exp.to_identifier(name, quoted=True).sql("duckdb")


def _table_ref(schema: str, name: str) -> str:
    return f"{_ident(schema)}.{_ident(name)}"


def _literal(text: str) -> str:
    return exp.Literal.string(text).sql("duckdb")


class DuckDBSource:
    dialect = "duckdb"
    supports_as_of = False

    def __init__(
        self, id: str, path: str | Path, *, memory_limit: str = "2GB", threads: int = 2
    ):
        self.id = id
        self.path = Path(path)
        self._config = {"memory_limit": memory_limit, "threads": threads}
        self._lock = threading.Lock()
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._stamp: tuple[int, int] | None = None
        self._data_time: datetime | None = None  # of the attached file
        self._catalog: tuple[tuple[int, int], Catalog] | None = None

    def __repr__(self) -> str:
        return f"DuckDBSource({self.id!r})"

    def _file_stamp(self) -> tuple[int, int]:
        st = self.path.stat()
        return st.st_ino, st.st_mtime_ns

    def _open(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:", config=self._config)
        try:
            conn.execute(f"ATTACH {_literal(str(self.path))} AS {_DB} (READ_ONLY)")
            conn.execute("SET enable_external_access = false")
            conn.execute("SET lock_configuration = true")
        except BaseException:
            conn.close()
            raise
        return conn

    def _cursor(self) -> duckdb.DuckDBPyConnection:
        return self._attached()[0]

    def _attached(self) -> tuple[duckdb.DuckDBPyConnection, datetime]:
        """A cursor on the current file and that file's data time. Attaches the file again
        after a rebuild."""
        with self._lock:
            st = self.path.stat()
            stamp = (st.st_ino, st.st_mtime_ns)
            if self._conn is None or stamp != self._stamp:
                # The old instance is not closed: a run on it ends on the old file, and
                # the instance is freed with its last cursor.
                self._conn = self._open()
                self._stamp = stamp
                self._data_time = _utc(st.st_mtime)
            cursor = self._conn.cursor()
            data_time = self._data_time
        cursor.execute(f"USE {_DB}")
        return cursor, data_time

    def run(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        limit: int = DEFAULT_LIMIT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        as_of: datetime | None = None,
    ) -> pa.Table:
        refuse_as_of(self, as_of)
        _one_select(sql)
        try:
            cursor, data_time = self._attached()
        except (duckdb.Error, OSError) as error:
            raise SourceError(f"{self.id}: {type(error).__name__}") from error
        fired = threading.Event()

        def stop() -> None:
            fired.set()
            cursor.interrupt()

        timer = threading.Timer(timeout_s, stop)
        timer.start()
        try:
            cursor.execute(sql, list(params))
            reader = cursor.to_arrow_reader(limit + 1)
            batches, rows = [], 0
            for batch in reader:
                batches.append(batch)
                rows += batch.num_rows
                if rows > limit:
                    break
            table = pa.Table.from_batches(batches, reader.schema)
        except duckdb.Error as error:
            if fired.is_set() or isinstance(error, duckdb.InterruptException):
                raise SourceTimeout(self.id, timeout_s) from error
            if isinstance(error, duckdb.IOException | duckdb.PermissionException):
                # Their text names a file path.
                raise SourceError(f"{self.id}: {type(error).__name__}") from None
            raise SourceError(str(error)) from error
        finally:
            timer.cancel()
            cursor.close()
        return with_data_time(capped(table, limit), data_time)

    def data_time(self) -> datetime:
        try:
            mtime = self.path.stat().st_mtime
        except OSError as error:
            raise SourceError(f"{self.id}: the data file is missing") from error
        return _utc(mtime)

    def check(self) -> None:
        try:
            cursor = self._cursor()
            try:
                cursor.execute("SELECT 1").fetchall()
            finally:
                cursor.close()
        except (duckdb.Error, OSError) as error:
            raise SourceError(f"{self.id}: {type(error).__name__}") from error

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            self._stamp = None

    def introspect(self) -> Catalog:
        """Tables, columns, keys, row counts, and column statistics of the file.

        One statement per table for the statistics (``SUMMARIZE``) and the samples; the
        catalog is kept until the file changes.
        """
        try:
            stamp = self._file_stamp()
            if self._catalog and self._catalog[0] == stamp:
                return self._catalog[1]
            cursor = self._cursor()
            try:
                catalog = self._introspect(cursor)
            finally:
                cursor.close()
        except (duckdb.Error, OSError) as error:
            raise SourceError(f"{self.id}: {type(error).__name__}") from error
        self._catalog = (stamp, catalog)
        return catalog

    def _introspect(self, cursor: duckdb.DuckDBPyConnection) -> Catalog:
        db = _literal(_DB)
        names = cursor.execute(
            f"SELECT schema_name, table_name FROM duckdb_tables() "
            f"WHERE database_name = {db} AND NOT internal "
            f"UNION ALL SELECT schema_name, view_name FROM duckdb_views() "
            f"WHERE database_name = {db} AND NOT internal ORDER BY 1, 2"
        ).fetchall()
        columns: dict[tuple[str, str], list[ColumnInfo]] = {}
        for schema, table, column, data_type, nullable in cursor.execute(
            f"SELECT schema_name, table_name, column_name, data_type, is_nullable "
            f"FROM duckdb_columns() WHERE database_name = {db} "
            f"ORDER BY schema_name, table_name, column_index"
        ).fetchall():
            columns.setdefault((schema, table), []).append(
                ColumnInfo(
                    name=column,
                    source_type=data_type,
                    type=field_type(data_type),
                    nullable=bool(nullable),
                )
            )
        keys: dict[tuple[str, str], dict[str, Any]] = {}
        for schema, table, kind, cols, ref_table, ref_cols in cursor.execute(
            f"SELECT schema_name, table_name, constraint_type, constraint_column_names, "
            f"referenced_table, referenced_column_names FROM duckdb_constraints() "
            f"WHERE database_name = {db} "
            f"AND constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')"
        ).fetchall():
            entry = keys.setdefault((schema, table), {"pk": [], "uk": [], "fk": []})
            if kind == "PRIMARY KEY":
                entry["pk"] = list(cols)
            elif kind == "UNIQUE":
                entry["uk"].append(list(cols))
            else:
                entry["fk"].append(
                    ForeignKey(
                        columns=list(cols),
                        to_table=f"{schema}.{ref_table}",
                        to_columns=list(ref_cols),
                    )
                )
        counts: list[int] = []
        if names:
            parts = ", ".join(
                f"(SELECT count(*) FROM {_table_ref(s, t)}) AS c{i}"
                for i, (s, t) in enumerate(names)
            )
            counts = list(cursor.execute(f"SELECT {parts}").fetchone())
        tables = []
        for (schema, table), rows in zip(names, counts or [None] * len(names)):
            cols = columns.get((schema, table), [])
            self._statistics(cursor, schema, table, cols)
            entry = keys.get((schema, table), {"pk": [], "uk": [], "fk": []})
            tables.append(
                TableInfo(
                    schema_name=schema,
                    name=table,
                    columns=cols,
                    row_count=rows,
                    primary_key=entry["pk"],
                    unique_keys=entry["uk"],
                    foreign_keys=entry["fk"],
                )
            )
        return Catalog(
            source_id=self.id,
            dialect=self.dialect,
            taken_at=datetime.now(timezone.utc).replace(tzinfo=None),
            tables=tables,
        )

    def _statistics(
        self, cursor: duckdb.DuckDBPyConnection, schema: str, table: str, cols: list[ColumnInfo]
    ) -> None:
        if not cols:
            return
        ref = _table_ref(schema, table)
        summary = {
            name: (unique, nulls, low, high)
            for name, unique, nulls, low, high in cursor.execute(
                f"SELECT column_name, approx_unique, null_percentage, min, max "
                f"FROM (SUMMARIZE {ref})"
            ).fetchall()
        }
        parts = ", ".join(
            f"(SELECT list(DISTINCT CAST(v AS VARCHAR) ORDER BY CAST(v AS VARCHAR)) "
            f"FROM (SELECT {_ident(c.name)} AS v FROM {ref} "
            f"WHERE {_ident(c.name)} IS NOT NULL LIMIT {SAMPLE_ROWS}))[1:{SAMPLES}]"
            for c in cols
        )
        samples = cursor.execute(f"SELECT {parts}").fetchone()
        for i, column in enumerate(cols):
            unique, nulls, low, high = summary.get(column.name, (None, None, None, None))
            column.min, column.max = low, high
            column.distinct_count = int(unique) if unique is not None else None
            column.null_rate = float(nulls) / 100 if nulls is not None else None
            column.samples = list(samples[i] or [])


def _utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)


def _one_select(sql: str) -> None:
    """One SELECT, by sqlglot and by DuckDB's own parser (two parsers, one answer)."""
    try:
        parse_select(sql, "duckdb")
    except QueryRejected as error:
        raise SourceError(str(error)) from error
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as error:
        raise SourceError(str(error)) from error
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise SourceError("Only one SELECT statement is allowed.")
