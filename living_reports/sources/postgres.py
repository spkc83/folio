"""``PostgresSource``: the portability proof. A DB-API driver and the dialect ``postgres``.

Each run opens one connection with read-only transactions and a statement timeout
(plan ruling 4). The DSN stays private: no error text, log line, or ``repr`` holds it.
"""
import threading
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import psycopg
import pyarrow as pa
from psycopg import errors
from psycopg.conninfo import conninfo_to_dict

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

SAMPLES = 3
# A catalog is kept this long; refresh() drops it at once.
CATALOG_TTL_S = 600.0
INTROSPECT_TIMEOUT_S = 20.0
CONNECT_TIMEOUT_S = 5  # when the DSN gives none: a host that never answers must not hang
_SYSTEM = "('pg_catalog', 'information_schema', 'pg_toast')"


def redact_dsn(dsn: str) -> str:
    """The DSN without its password: ``postgresql://user@host:port/dbname``."""
    try:
        parts = conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001 - any parse failure gives the same safe text
        return "postgresql://…"
    user, host = parts.get("user", ""), parts.get("host", "")
    port, dbname = parts.get("port") or 5432, parts.get("dbname", "")
    return f"postgresql://{user}@{host}:{port}/{dbname}"


def prepare(sql: str) -> str:
    """Escape ``%`` for psycopg: double each ``%`` except the ``%s`` placeholders.

    Inside a single-quoted literal every ``%`` is doubled (``''`` is an escaped quote,
    not the end of the literal). Outside a literal only ``%s`` stays as it is.

    A character scan is enough because the input is SQL that sqlglot rendered from a
    compiled query: it has no comments, no dollar-quoted strings, and no ``E''`` strings,
    and it puts spaces around operators, so ``%s`` outside a literal is always a
    placeholder. ``run`` calls this only when it passes parameters. Do not pass
    parameters with SQL that a model wrote (exploratory SQL): that SQL has none, and it
    goes to the driver unchanged.
    """
    out: list[str] = []
    in_literal = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if in_literal and sql[i + 1 : i + 2] == "'":
                out.append("''")
                i += 2
                continue
            in_literal = not in_literal
        elif ch == "%":
            if not in_literal and sql[i + 1 : i + 2] == "s":
                out.append("%s")
                i += 2
                continue
            out.append("%%")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _table(rows: list[tuple], names: list[str]) -> pa.Table:
    """An Arrow table from at most ``limit + 1`` rows (plan ruling 2: not a data scan)."""
    columns = list(zip(*rows)) if rows else [()] * len(names)
    arrays = [pa.array(list(c)) if rows else pa.array([], pa.null()) for c in columns]
    return pa.Table.from_arrays(arrays, names=names)


class PostgresSource:
    dialect = "postgres"
    supports_as_of = False

    def __init__(self, id: str, dsn: str, *, application_name: str = "folio"):
        self.id = id
        self._dsn = dsn
        self._application_name = application_name
        self._catalog: tuple[float, Catalog] | None = None
        self._catalog_lock = threading.Lock()

    def __repr__(self) -> str:
        return f"PostgresSource({self.id!r}, {redact_dsn(self._dsn)!r})"

    def _connect(self) -> psycopg.Connection:
        extra: dict[str, Any] = {}
        try:
            if "connect_timeout" not in conninfo_to_dict(self._dsn):
                extra["connect_timeout"] = CONNECT_TIMEOUT_S
        except psycopg.Error:
            raise SourceError(f"{self.id}: cannot connect") from None
        try:
            return psycopg.connect(
                self._dsn,
                autocommit=True,
                application_name=self._application_name,
                options="-c default_transaction_read_only=on",
                **extra,
            )
        except psycopg.Error:
            # No chained cause: the driver's message names the host.
            raise SourceError(f"{self.id}: cannot connect") from None

    def _error(self, error: psycopg.Error) -> SourceError:
        diag = getattr(error, "diag", None)
        message = getattr(diag, "message_primary", None) or type(error).__name__
        return SourceError(f"{self.id}: {message}")

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
        try:
            parse_select(sql, "postgres")
        except QueryRejected as error:
            raise SourceError(str(error)) from error
        params = list(params)
        # The client cursor holds the whole result, so the server stops at limit + 1 rows.
        # The limit is an int that Folio sets; it is never a value from a request.
        body = sql.strip().rstrip(";").rstrip()
        wrapped = f"SELECT * FROM ({body}) AS q LIMIT {int(limit) + 1}"
        with self._connect() as conn:
            try:
                conn.execute(f"SET statement_timeout = {max(1, int(timeout_s * 1000))}")
                data_time = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
                cursor = conn.cursor()
                # psycopg reads % as a format character only when parameters are passed.
                cursor.execute(prepare(wrapped) if params else wrapped, params or None)
                rows = cursor.fetchmany(limit + 1)
                names = [d.name for d in cursor.description or []]
            except errors.QueryCanceled as error:
                raise SourceTimeout(self.id, timeout_s) from error
            except psycopg.Error as error:
                raise self._error(error) from error
        return with_data_time(capped(_table(rows, names), limit), data_time)

    def data_time(self) -> datetime:
        """The server's time now: plain Postgres has no data version (plan ruling 3)."""
        with self._connect() as conn:
            try:
                return conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
            except psycopg.Error as error:
                raise self._error(error) from error

    def check(self) -> None:
        with self._connect() as conn:
            try:
                conn.execute("SELECT 1").fetchone()
            except psycopg.Error as error:
                raise self._error(error) from error

    def refresh(self) -> None:
        """Drop the kept catalog; the next ``introspect`` reads the server again."""
        with self._catalog_lock:
            self._catalog = None

    def introspect(self) -> Catalog:
        """Tables, columns, keys, estimated row counts, and ``pg_stats`` statistics.

        One statement each for the tables, the columns, the keys, the row counts
        (``pg_class.reltuples``, which ``ANALYZE`` keeps), and the statistics. Views have no
        row count and no ``pg_stats`` rows. The catalog is kept for ``CATALOG_TTL_S``.
        """
        with self._catalog_lock:
            if self._catalog and time.monotonic() - self._catalog[0] < CATALOG_TTL_S:
                return self._catalog[1]
            with self._connect() as conn:
                try:
                    conn.execute(f"SET statement_timeout = {int(INTROSPECT_TIMEOUT_S * 1000)}")
                    catalog = self._introspect(conn)
                except errors.QueryCanceled as error:
                    raise SourceTimeout(self.id, INTROSPECT_TIMEOUT_S) from error
                except psycopg.Error as error:
                    raise self._error(error) from error
            self._catalog = (time.monotonic(), catalog)
            return catalog

    def _introspect(self, conn: psycopg.Connection) -> Catalog:
        names = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            f"WHERE table_schema NOT IN {_SYSTEM} AND table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY 1, 2"
        ).fetchall()
        columns: dict[tuple[str, str], list[ColumnInfo]] = {}
        for schema, table, column, data_type, nullable in conn.execute(
            "SELECT table_schema, table_name, column_name, data_type, is_nullable "
            f"FROM information_schema.columns WHERE table_schema NOT IN {_SYSTEM} "
            "ORDER BY table_schema, table_name, ordinal_position"
        ).fetchall():
            columns.setdefault((schema, table), []).append(
                ColumnInfo(
                    name=column,
                    source_type=data_type,
                    type=field_type(data_type),
                    nullable=nullable == "YES",
                )
            )
        # pg_constraint keeps the key columns in order (conkey, confkey).
        keys: dict[tuple[str, str], dict[str, Any]] = {}
        for schema, table, kind, cols, ref_schema, ref_table, ref_cols in conn.execute(
            "SELECT n.nspname, c.relname, con.contype, "
            "ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(num, pos) "
            "JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num "
            "ORDER BY k.pos)::text[], "
            "fn.nspname, fc.relname, "
            "ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(num, pos) "
            "JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.num "
            "ORDER BY k.pos)::text[] "
            "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_class fc ON fc.oid = con.confrelid "
            "LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace "
            f"WHERE con.contype IN ('p', 'u', 'f') AND n.nspname NOT IN {_SYSTEM}"
        ).fetchall():
            entry = keys.setdefault((schema, table), {"pk": [], "uk": [], "fk": []})
            if kind == "p":
                entry["pk"] = list(cols)
            elif kind == "u":
                entry["uk"].append(list(cols))
            else:
                entry["fk"].append(
                    ForeignKey(
                        columns=list(cols),
                        to_table=f"{ref_schema}.{ref_table}",
                        to_columns=list(ref_cols),
                    )
                )
        estimates = {
            (schema, table): tuples
            for schema, table, tuples in conn.execute(
                "SELECT n.nspname, c.relname, c.reltuples FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                f"WHERE c.relkind IN ('r', 'p', 'm') AND n.nspname NOT IN {_SYSTEM}"
            ).fetchall()
        }
        # reltuples is -1 (or 0 before PG 14) for a table that ANALYZE has not read.
        counts = [
            round(estimates[key]) if estimates.get(key, -1) >= 0 else None for key in names
        ]
        stats = {
            (schema, table, column): (n_distinct, null_frac, samples)
            for schema, table, column, n_distinct, null_frac, samples in conn.execute(
                "SELECT schemaname, tablename, attname, n_distinct, null_frac, "
                f"(most_common_vals::text::text[])[1:{SAMPLES}] FROM pg_stats "
                f"WHERE schemaname NOT IN {_SYSTEM}"
            ).fetchall()
        }
        tables = []
        for (schema, table), rows in zip(names, counts):
            cols = columns.get((schema, table), [])
            for c in cols:
                n_distinct, null_frac, samples = stats.get((schema, table, c.name), (None,) * 3)
                if n_distinct is not None:
                    c.distinct_count = (
                        round(-n_distinct * rows) if n_distinct < 0 and rows else int(n_distinct)
                    )
                c.null_rate = float(null_frac) if null_frac is not None else None
                c.samples = [str(v) for v in samples or []]
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
        taken_at = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
        return Catalog(source_id=self.id, dialect=self.dialect, taken_at=taken_at, tables=tables)

