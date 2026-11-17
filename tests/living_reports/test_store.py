from datetime import datetime

import pytest

from living_reports.reports.editions import EditionStore
from living_reports.reports.store import ReportStore, StaleReport, StoredReport, Thread
from living_reports.semantic.kinds import ReportPayload
from living_reports.spec.models import ReportSpec, Section
from living_reports.store import Store, get_store, reset_store


def report(title: str = "Loss") -> StoredReport:
    spec = ReportSpec(
        title=title,
        objective="why",
        sections=[Section(id="s1", heading="H", intent="i", blocks=[])],
    )
    return StoredReport(
        author="ana", tier="team", source_id="duckdb_demo", view="narrative", spec=spec,
        facts={}, queries={}, query_defs={},
    )


def test_one_store_per_process(orders_file, tmp_path):
    first = get_store()
    assert get_store() is first
    assert first.path == tmp_path / "lr" / "app.sqlite"
    assert first.path.exists()
    assert first.row("PRAGMA journal_mode")[0] == "wal"
    reset_store()
    assert get_store() is not first


def test_store_and_graph_share_one_connection(store):
    assert store.graph.graph.backend._conn is store.conn


def test_transaction_rolls_back_everything(store):
    reports = ReportStore(store)
    with pytest.raises(RuntimeError), store.transaction():
        saved = reports.save(report())
        store.graph.put("report:x", "Report", ReportPayload(title="x", tier="team", author="ana"))
        raise RuntimeError("boom")
    assert reports.get(saved.id) is None
    assert store.graph.slice().node("report:x") is None


def test_nested_store_transactions_join(store):
    with pytest.raises(RuntimeError), store.transaction():
        with store.transaction():
            store.execute("INSERT INTO app_state VALUES ('k', 'v')")
        raise RuntimeError("boom")
    assert store.row("SELECT value FROM app_state WHERE key = 'k'") is None


def test_report_round_trip_and_stale_check(store, clock_store):
    reports = ReportStore(store)
    a = reports.save(report("A"))
    clock_store.set(datetime(2024, 1, 2))
    b = reports.save(report("B"))
    assert reports.get(a.id).spec.title == "A"
    assert [r.id for r in reports.list_reports()] == [b.id, a.id]
    stale = reports.get(a.id)
    reports.save(reports.get(a.id))
    with pytest.raises(StaleReport):
        reports.save(stale)


def test_edition_freeze_and_lookup(store):
    reports = ReportStore(store)
    rid = reports.save(report()).id
    editions = EditionStore(store, reports)
    first = editions.freeze(rid)
    assert editions.freeze(rid).number == first.number == 1
    assert first.known_at is not None
    assert editions.newest(rid).number == 1
    assert editions.latest(rid) is None
    editions.mark_published(rid, 1)
    assert editions.latest(rid).number == 1
    assert editions.get(rid, 1).status == "published"
    assert editions.get(rid, 9) is None


def test_threads_round_trip(store):
    reports = ReportStore(store)
    rid = reports.save(report()).id
    t = reports.save_thread(Thread(report_id=rid, section_id="s1"))
    assert reports.get_thread(t.id).section_id == "s1"
    assert [x.id for x in reports.threads_for(rid)] == [t.id]
    assert reports.get_thread("nope") is None


def test_a_report_saved_with_a_snapshot_reads_as_the_demo_source(store):
    import json
    reports = ReportStore(store)
    saved = reports.save(report())
    row = json.loads(store.row("SELECT payload FROM reports WHERE id = ?", [saved.id])[0])
    row.pop("source_id")
    row["snapshot"] = "s2"
    for fact in row["facts"].values():
        fact.pop("source_id"), fact.pop("data_time", None)
        fact["snapshot"] = "s2"
    store.execute("UPDATE reports SET payload = ? WHERE id = ?", [json.dumps(row), saved.id])
    again = reports.get(saved.id)
    assert again.source_id == "duckdb_demo"
    assert {f.source_id for f in again.facts.values()} <= {"duckdb_demo"}


@pytest.fixture
def clock_store(store):
    class Clock:
        now = datetime(2024, 1, 1)

        def set(self, when):
            self.now = when

        def __call__(self):
            return self.now

    clock = Clock()
    store.clock = clock
    return clock


def test_memory_store_needs_no_files():
    s = Store()
    assert s.row("SELECT 1") == (1,)
    s.close()
