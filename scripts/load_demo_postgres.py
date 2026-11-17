"""Load the tables of the active demo (``LR_DEMO``) into Postgres, at demo moment 2.

Run: .venv/bin/python scripts/load_demo_postgres.py [--source <path>] [--dsn <dsn>]
A generated demo (retail, fraud, hr) needs no --source: the script generates its data when it
is missing. The orders demo needs its Parquet file as --source. Without --dsn, the script reads
LR_PG_DSN. It prints row counts only, never the DSN.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

import duckdb
import psycopg
from psycopg import sql

from living_reports.config import get_settings
from living_reports.demos import get_demo
from living_reports.sources.build import _generated

CHUNK = 1 << 20
# DuckDB type name prefix -> Postgres type. Longest prefixes first; others become TEXT.
_TYPES = [
    ("TIMESTAMP", "TIMESTAMP"),
    ("DECIMAL", "DOUBLE PRECISION"),
    ("DOUBLE", "DOUBLE PRECISION"),
    ("FLOAT", "DOUBLE PRECISION"),
    ("BIGINT", "BIGINT"),
    ("TINYINT", "INTEGER"),
    ("SMALLINT", "INTEGER"),
    ("INTEGER", "INTEGER"),
    ("BOOLEAN", "BOOLEAN"),
    ("DATE", "DATE"),
    ("VARCHAR", "TEXT"),
]


def pg_type(duckdb_type: str) -> str:
    name = duckdb_type.upper()
    return next((pg for prefix, pg in _TYPES if name.startswith(prefix)), "TEXT")


def load(source_path: Path, dsn: str, schema: str = "public") -> dict[str, int]:
    """Write each demo table to CSV with DuckDB, then COPY it into ``schema``."""
    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as duck:
        files, columns = {}, {}
        demo = get_demo()
        for name, select in demo.tables(source_path, 2).items():
            columns[name] = duck.execute(f"DESCRIBE {select}").fetchall()
            files[name] = Path(tmp) / f"{name}.csv"
            duck.execute(f"COPY ({select}) TO '{files[name]}' (FORMAT CSV, HEADER)")
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            for name, path in files.items():
                table = sql.Identifier(schema, name)
                definition = sql.SQL(", ").join(
                    sql.SQL("{} {}").format(sql.Identifier(c[0]), sql.SQL(pg_type(c[1])))
                    for c in columns[name]
                )
                conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(table))
                conn.execute(sql.SQL("CREATE TABLE {} ({})").format(table, definition))
                copy_sql = sql.SQL("COPY {} FROM STDIN (FORMAT CSV, HEADER)").format(table)
                with conn.cursor() as cur, cur.copy(copy_sql) as copy, path.open("rb") as f:
                    while chunk := f.read(CHUNK):
                        copy.write(chunk)
                key = sql.SQL(", ").join(sql.Identifier(c) for c in demo.keys[name])
                conn.execute(sql.SQL("ALTER TABLE {} ADD PRIMARY KEY ({})").format(table, key))
                conn.execute(sql.SQL("ANALYZE {}").format(table))
                counts[name] = conn.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(table)
                ).fetchone()[0]
    return counts


def main(source_path: Path, dsn: str) -> None:
    for name, rows in load(source_path, dsn).items():
        print(f"{name}: {rows:,} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, help="The orders demo's Parquet file.")
    parser.add_argument("--dsn", default=os.environ.get("LR_PG_DSN"))
    args = parser.parse_args()
    if not args.dsn:
        sys.exit("Give --dsn or set LR_PG_DSN.")
    if args.source is None:
        if get_demo().generate is None:
            sys.exit("The orders demo needs --source.")
        args.source = _generated(get_settings()).parent
    main(args.source, args.dsn)
