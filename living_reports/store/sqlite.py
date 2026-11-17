"""The app's one store: SQLite, one connection per process, shared with the graph.

App tables (reports, editions, caches) and the graph live in one database file, so one
transaction can write an edition row and its graph node together.
"""

import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from folio_graph import SQLiteBackend
from folio_graph.backend import ts
from folio_graph.engine import utcnow
from living_reports.semantic.graph import FolioGraph

__all__ = ["Store", "ts"]

_APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS definitions (report_id TEXT, version INTEGER, payload TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, report_id TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS editions (
  report_id TEXT, number INTEGER, status TEXT, payload TEXT, PRIMARY KEY (report_id, number)
);
CREATE TABLE IF NOT EXISTS check_runs (
  report_id TEXT, number INTEGER, content_hash TEXT, payload TEXT, PRIMARY KEY (report_id, number)
);
CREATE TABLE IF NOT EXISTS signatures (
  report_id TEXT, number INTEGER, role TEXT, signer TEXT, content_hash TEXT, signed_at TEXT,
  PRIMARY KEY (report_id, number, role)
);
CREATE TABLE IF NOT EXISTS changes (id TEXT PRIMARY KEY, seq INTEGER, status TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS query_cache (
  query_hash TEXT, source_id TEXT, data_time TEXT, payload TEXT,
  PRIMARY KEY (query_hash, source_id, data_time)
);
CREATE TABLE IF NOT EXISTS answer_cache (key TEXT PRIMARY KEY, report_id TEXT, view TEXT);
CREATE TABLE IF NOT EXISTS certified_questions (
  id TEXT NOT NULL, question TEXT NOT NULL, query TEXT NOT NULL, report_id TEXT NOT NULL,
  section_id TEXT NOT NULL, graph_version INTEGER NOT NULL, definitions TEXT NOT NULL,
  expected TEXT NOT NULL, verified_by TEXT NOT NULL, verified_at TEXT NOT NULL,
  edition INTEGER NOT NULL, content_hash TEXT NOT NULL,
  PRIMARY KEY (id, report_id, edition)
);
CREATE TABLE IF NOT EXISTS discovery_runs (
  id TEXT PRIMARY KEY, source_id TEXT, started_at TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS discovery_probes (proposal_id TEXT PRIMARY KEY, run_id TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS proposal_lineage (proposal_id TEXT PRIMARY KEY, proposers TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback (
  report_id TEXT, section_id TEXT, user TEXT, signal TEXT, at TEXT,
  PRIMARY KEY (report_id, section_id, user)
);
CREATE TABLE IF NOT EXISTS llm_usage (
  turn_id TEXT, model TEXT, tier TEXT, input_tokens INTEGER, output_tokens INTEGER,
  created_at TEXT
);
"""


class Store:
    """One SQLite connection, guarded by one lock, for every thread of the process."""

    def __init__(self, path: str | Path = ":memory:", clock: Callable[[], datetime] = utcnow):
        self.path = Path(path) if str(path) != ":memory:" else None
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.lock = threading.RLock()
        self.clock = clock
        with self.lock:
            if self.path is not None:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA foreign_keys=OFF")
            self._migrate()
            self.conn.executescript(_APP_SCHEMA)
        backend = SQLiteBackend(connection=self.conn, lock=self.lock)
        self.graph = FolioGraph(backend, clock=lambda: self.clock())

    def _migrate(self) -> None:
        """An older certified_questions table (keyed by question only, no edition) is
        dropped. Its rows cannot bind to an edition; the next publish writes them again."""
        info = self.conn.execute("PRAGMA table_info(certified_questions)").fetchall()
        key = [row[1] for row in sorted(info, key=lambda r: r[5]) if row[5]]
        if info and key != ["id", "report_id", "edition"]:
            self.conn.execute("DROP TABLE certified_questions")
        # A query cache keyed by snapshot name is dropped: it is only a cache, and a
        # snapshot name cannot tell old data from new.
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(query_cache)")]
        if columns and "data_time" not in columns:
            self.conn.execute("DROP TABLE query_cache")

    @staticmethod
    def ts(value: datetime | None) -> str | None:
        return ts(value)

    def now(self) -> datetime:
        return self.clock()

    def rows(self, sql: str, params: Sequence = ()) -> list[tuple]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def row(self, sql: str, params: Sequence = ()) -> tuple | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Sequence = ()) -> None:
        """One write. Inside an open transaction it joins it; otherwise it commits alone."""
        with self.lock:
            self.conn.execute(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` under the lock. A nested call joins the outer transaction."""
        with self.lock:
            if self.conn.in_transaction:
                yield self.conn
                return
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def close(self) -> None:
        with self.lock:
            self.conn.close()
