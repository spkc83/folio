import sqlite3
import threading
from datetime import datetime

import pytest

from folio_graph import EdgeChange, NodeChange
from folio_graph.backend import SQLiteBackend
from folio_graph.engine import Graph

METRIC = {"label": "Fraud loss", "definition": "Total loss.", "expr": {"agg": "sum"}}


def _graph(backend, clock, kinds) -> Graph:
    g = Graph(backend, clock=clock)
    for spec in kinds:
        g.register(spec)
    return g


def _put(g: Graph, node_id: str, kind: str, payload: dict):
    p = g.propose(NodeChange(id=node_id, kind=kind, payload=payload), "ana", "test")
    return g.confirm(p.id, "omar")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    yield c
    c.close()


def test_shared_connection_sees_the_same_rows(conn, clock, kinds):
    backend = SQLiteBackend(connection=conn, lock=threading.RLock())
    g = _graph(backend, clock, kinds)
    _put(g, "fraud_loss", "Metric", METRIC)
    assert conn.execute("SELECT count(*) FROM nodes WHERE id = 'fraud_loss'").fetchone()[0] >= 1


def test_shared_connection_needs_a_lock(conn):
    with pytest.raises(ValueError, match="lock"):
        SQLiteBackend(connection=conn)


def test_nested_transaction_joins_the_outer_one(conn, clock, kinds):
    g = _graph(SQLiteBackend(connection=conn, lock=threading.RLock()), clock, kinds)
    conn.execute("BEGIN IMMEDIATE")
    _put(g, "fraud_loss", "Metric", METRIC)
    assert g.as_of().node("fraud_loss") is not None
    conn.execute("ROLLBACK")
    assert g.as_of().node("fraud_loss") is None


def test_version_stamp_grows_with_every_write(clock, kinds):
    b = SQLiteBackend()
    g = _graph(b, clock, kinds)
    t0, t1, t2, t3 = (datetime(2024, 1, d) for d in (1, 2, 3, 4))
    stamp0 = b.version_stamp(t0)
    clock.set(t1)
    _put(g, "fraud_loss", "Metric", METRIC)
    _put(g, "r1", "Report", {"title": "R"})
    assert b.version_stamp(t1) == stamp0 + 2
    clock.set(t2)
    p = g.propose(EdgeChange(kind="uses", from_id="r1", to_id="fraud_loss"), "ana", "t")
    g.confirm(p.id, "omar")
    assert b.version_stamp(t2) == stamp0 + 3
    clock.set(t3)
    g.retire("r1", "omar")
    assert b.version_stamp(t3) == stamp0 + 4
    assert b.version_stamp(t1) < b.version_stamp(t3)
    assert g.as_of(known_at=t1).version == b.version_stamp(t1)


def test_version_stamp_before_any_write_is_zero():
    assert SQLiteBackend().version_stamp(datetime(2024, 1, 1)) == 0
