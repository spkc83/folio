"""Storage behind the graph. The SQL backend runs on SQLite from the standard library.

Rows are never updated except to close ``recorded_to``. Times are naive UTC, stored as
ISO 8601 text with microseconds, so text order is time order.
"""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import astuple
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from folio_graph.model import Edge, Node, Proposal

Table = Literal["nodes", "edges"]
Statuses = tuple[str, ...]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
  version INTEGER NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
  recorded_from TEXT NOT NULL, recorded_to TEXT, proposal TEXT
);
CREATE TABLE IF NOT EXISTS edges (
  id TEXT NOT NULL, kind TEXT NOT NULL, from_id TEXT NOT NULL, to_id TEXT NOT NULL, role TEXT,
  payload TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
  valid_from TEXT NOT NULL, valid_to TEXT, recorded_from TEXT NOT NULL, recorded_to TEXT,
  proposal TEXT
);
CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, status TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS nodes_id ON nodes (id, recorded_to);
CREATE INDEX IF NOT EXISTS edges_id ON edges (id, recorded_to);
CREATE INDEX IF NOT EXISTS edges_from ON edges (from_id, kind);
CREATE INDEX IF NOT EXISTS edges_to ON edges (to_id, kind);
"""

_NODE_COLS = "id, kind, payload, status, version, valid_from, valid_to, recorded_from, recorded_to"
_EDGE_COLS = (
    "id, kind, from_id, to_id, role, payload, status, version, "
    "valid_from, valid_to, recorded_from, recorded_to"
)
_IN_FORCE = (
    "valid_from <= :v AND (valid_to IS NULL OR valid_to > :v) "
    "AND recorded_from <= :k AND (recorded_to IS NULL OR recorded_to > :k)"
)


def ts(value: datetime | None) -> str | None:
    return value.isoformat(timespec="microseconds") if value is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _node(row: tuple) -> Node:
    i, kind, payload, status, version, vf, vt, rf, rt = row
    return Node(i, kind, json.loads(payload), status, version, _dt(vf), _dt(vt), _dt(rf), _dt(rt))


def _edge(row: tuple) -> Edge:
    i, kind, src, dst, role, payload, status, version, vf, vt, rf, rt = row
    return Edge(
        i,
        kind,
        src,
        dst,
        role,
        json.loads(payload),
        status,
        version,
        _dt(vf),
        _dt(vt),
        _dt(rf),
        _dt(rt),
    )


class Writer(Protocol):
    def insert(self, entity: Node | Edge, proposal: str | None = None) -> None: ...
    def close_open(self, table: Table, entity_id: str, at: datetime) -> None: ...
    def close_candidate(self, proposal_id: str, at: datetime) -> None: ...
    def save_proposal(self, proposal: Proposal) -> None: ...


class GraphBackend(Protocol):
    def nodes(
        self,
        valid_at: datetime,
        known_at: datetime,
        *,
        kind: str | None = None,
        ids: list[str] | None = None,
        statuses: Statuses = ("confirmed",),
    ) -> list[Node]: ...
    def edges(
        self,
        valid_at: datetime,
        known_at: datetime,
        *,
        kind: str | list[str] | None = None,
        from_id: str | None = None,
        to_id: str | None = None,
        statuses: Statuses = ("confirmed",),
    ) -> list[Edge]: ...
    def open_version(self, table: Table, entity_id: str) -> Node | Edge | None: ...
    def max_version(self, table: Table, entity_id: str) -> int: ...
    def history(self, entity_id: str) -> list[Node | Edge]: ...
    def reachable(
        self,
        start: str,
        edge_kinds: list[str] | None,
        direction: Literal["in", "out"],
        valid_at: datetime,
        known_at: datetime,
        max_hops: int,
    ) -> list[tuple[str, int]]: ...
    def proposal(self, proposal_id: str) -> Proposal | None: ...
    def proposals(self, status: str) -> list[Proposal]: ...
    def version_stamp(self, known_at: datetime) -> int: ...
    def transaction(self) -> "contextmanager[Writer]": ...


class _SQLiteWriter:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, entity: Node | Edge, proposal: str | None = None) -> None:
        values = list(astuple(entity))
        if isinstance(entity, Node):
            values[2] = json.dumps(values[2], sort_keys=True)
            table, width = "nodes", 10
        else:
            values[5] = json.dumps(values[5], sort_keys=True)
            table, width = "edges", 13
        values = [ts(v) if isinstance(v, datetime) else v for v in values] + [proposal]
        self.conn.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * width)})", values)

    def close_open(self, table: Table, entity_id: str, at: datetime) -> None:
        self.conn.execute(
            f"UPDATE {table} SET recorded_to = ? WHERE id = ? AND status = 'confirmed' "
            "AND valid_to IS NULL AND recorded_to IS NULL",
            [ts(at), entity_id],
        )

    def close_candidate(self, proposal_id: str, at: datetime) -> None:
        for table in ("nodes", "edges"):
            self.conn.execute(
                f"UPDATE {table} SET recorded_to = ? WHERE proposal = ? "
                "AND status = 'candidate' AND recorded_to IS NULL",
                [ts(at), proposal_id],
            )

    def save_proposal(self, proposal: Proposal) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO proposals VALUES (?, ?, ?)",
            [proposal.id, proposal.status, proposal.model_dump_json()],
        )


class SQLiteBackend:
    """One connection per backend, shared across threads under a lock.

    A file path turns on write-ahead logging, so readers in other processes do not block.
    With ``connection``, the backend uses that connection and ``lock`` (both owned by the
    caller) instead of its own, so the caller's tables and the graph share transactions.
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        connection: sqlite3.Connection | None = None,
        lock: "threading.RLock | None" = None,
    ):
        if connection is not None:
            if lock is None:
                raise ValueError("A shared connection needs the lock that guards it")
            self._conn, self._lock = connection, lock
        else:
            self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            self._lock = threading.RLock()
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def _rows(self, sql: str, params) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @staticmethod
    def _in(column: str, values, params: dict, prefix: str) -> str:
        names = [f"{prefix}{i}" for i in range(len(values))]
        params.update(zip(names, values))
        return f"{column} IN ({', '.join(':' + n for n in names)})"

    def nodes(self, valid_at, known_at, *, kind=None, ids=None, statuses=("confirmed",)):
        params: dict = {"v": ts(valid_at), "k": ts(known_at)}
        where = [_IN_FORCE, self._in("status", statuses, params, "s")]
        if kind is not None:
            where.append("kind = :kind")
            params["kind"] = kind
        if ids is not None:
            if not ids:
                return []
            where.append(self._in("id", ids, params, "i"))
        sql = f"SELECT {_NODE_COLS} FROM nodes WHERE {' AND '.join(where)} ORDER BY id, version"
        return [_node(r) for r in self._rows(sql, params)]

    def edges(
        self, valid_at, known_at, *, kind=None, from_id=None, to_id=None, statuses=("confirmed",)
    ):
        params: dict = {"v": ts(valid_at), "k": ts(known_at)}
        where = [_IN_FORCE, self._in("status", statuses, params, "s")]
        if kind is not None:
            kinds = [kind] if isinstance(kind, str) else list(kind)
            if not kinds:
                return []
            where.append(self._in("kind", kinds, params, "e"))
        for column, value in (("from_id", from_id), ("to_id", to_id)):
            if value is not None:
                where.append(f"{column} = :{column}")
                params[column] = value
        sql = f"SELECT {_EDGE_COLS} FROM edges WHERE {' AND '.join(where)} ORDER BY id, version"
        return [_edge(r) for r in self._rows(sql, params)]

    def open_version(self, table: Table, entity_id: str):
        cols, make = (_NODE_COLS, _node) if table == "nodes" else (_EDGE_COLS, _edge)
        rows = self._rows(
            f"SELECT {cols} FROM {table} WHERE id = ? AND status = 'confirmed' "
            "AND valid_to IS NULL AND recorded_to IS NULL",
            [entity_id],
        )
        return make(rows[0]) if rows else None

    def max_version(self, table: Table, entity_id: str) -> int:
        rows = self._rows(
            f"SELECT coalesce(max(version), 0) FROM {table} WHERE id = ? AND status = 'confirmed'",
            [entity_id],
        )
        return rows[0][0]

    def history(self, entity_id: str) -> list[Node | Edge]:
        nodes = self._rows(f"SELECT {_NODE_COLS} FROM nodes WHERE id = ?", [entity_id])
        edges = self._rows(f"SELECT {_EDGE_COLS} FROM edges WHERE id = ?", [entity_id])
        found = [_node(r) for r in nodes] + [_edge(r) for r in edges]
        return sorted(found, key=lambda e: (e.recorded_from, e.version))

    def reachable(self, start, edge_kinds, direction, valid_at, known_at, max_hops):
        params: dict = {"v": ts(valid_at), "k": ts(known_at), "start": start, "max": max_hops}
        kinds = ""
        if edge_kinds is not None:
            kinds = " AND " + self._in("kind", edge_kinds, params, "e")
        near, far = ("to_id", "from_id") if direction == "in" else ("from_id", "to_id")
        sql = f"""
            WITH RECURSIVE live AS (
              SELECT from_id, to_id FROM edges
              WHERE status = 'confirmed' AND {_IN_FORCE}{kinds}
            ),
            walk(id, hops) AS (
              SELECT :start, 0
              UNION
              SELECT live.{far}, walk.hops + 1 FROM live JOIN walk ON live.{near} = walk.id
              WHERE walk.hops < :max
            )
            SELECT id, min(hops) FROM walk WHERE id <> :start GROUP BY id ORDER BY id
        """
        return [(r[0], r[1]) for r in self._rows(sql, params)]

    def proposal(self, proposal_id: str) -> Proposal | None:
        rows = self._rows("SELECT body FROM proposals WHERE id = ?", [proposal_id])
        return Proposal.model_validate_json(rows[0][0]) if rows else None

    def proposals(self, status: str) -> list[Proposal]:
        rows = self._rows("SELECT body FROM proposals WHERE status = ? ORDER BY id", [status])
        return [Proposal.model_validate_json(r[0]) for r in rows]

    def version_stamp(self, known_at: datetime) -> int:
        """Confirmed rows recorded up to ``known_at``. Every write adds at least one."""
        sql = (
            "SELECT (SELECT count(*) FROM nodes WHERE status = 'confirmed' AND recorded_from <= :k)"
            " + (SELECT count(*) FROM edges WHERE status = 'confirmed' AND recorded_from <= :k)"
        )
        return self._rows(sql, {"k": ts(known_at)})[0][0]

    @contextmanager
    def transaction(self) -> Iterator[_SQLiteWriter]:
        """A write transaction. Inside an open transaction it joins it; the outer one decides."""
        with self._lock:
            if self._conn.in_transaction:
                yield _SQLiteWriter(self._conn)
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield _SQLiteWriter(self._conn)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
