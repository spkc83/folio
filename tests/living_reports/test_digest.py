"""Digest semantics: "new" for 24 hours, a seven-day window, ranked top changes, suggestions."""
import json
from datetime import datetime, timedelta

import pytest

from living_reports.harness.builder import AskContext
from living_reports.reports.certified import definitions_hash
from living_reports.reports.changes import ChangeItem, _save, open_changes
from living_reports.reports.digest import build_digest
from living_reports.reports.store import ReportStore

T0 = datetime(2030, 1, 7, 9, 0)  # after the seed, so the model is visible
QUERY = {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]}


@pytest.fixture
def clock(store):
    now = [T0]
    store.clock = lambda: now[0]
    return now


@pytest.fixture
def ctx(store, clock):
    return AskContext(store=store, reports=ReportStore(store), source_id="duckdb_demo")


def item(id, kind, affected=(), **kw):
    return ChangeItem(id=id, kind=kind, title=f"Title {id}", detail="d",
                      affected_reports=list(affected), **kw)


def certify_row(store, cid="c1", question="Where is fraud loss concentrated?"):
    store.execute(
        "INSERT INTO certified_questions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [cid, question, json.dumps(QUERY), "r1", "s1", 1,
         definitions_hash(QUERY, store.graph.model()), "{}", "omar", store.ts(T0), 1, "h"],
    )


def test_new_for_twenty_four_hours(ctx, clock):
    _save(item("a", "new_data"), ctx)
    clock[0] = T0 + timedelta(hours=23, minutes=59)
    [listed] = open_changes(ctx)
    assert listed.detected_at == T0 and listed.is_new is True
    clock[0] = T0 + timedelta(hours=24)
    assert open_changes(ctx)[0].is_new is False


def test_update_keeps_the_first_detected_at(ctx, clock):
    _save(item("a", "new_data"), ctx)
    clock[0] = T0 + timedelta(hours=2)
    _save(item("a", "new_data", attention=["r1"]), ctx)
    [listed] = open_changes(ctx)
    assert listed.detected_at == T0 and listed.attention == ["r1"]


def test_is_new_is_not_stored(ctx, store):
    _save(item("a", "new_data"), ctx)
    payload = json.loads(store.row("SELECT payload FROM changes")[0])
    assert "is_new" not in payload and payload["detected_at"]


def test_old_change_items_are_not_new(ctx, store):
    old = {"id": "old", "kind": "new_data", "title": "Old", "detail": "d"}
    store.execute("INSERT INTO changes VALUES (?, ?, ?, ?)", ["old", 1, "open", json.dumps(old)])
    [listed] = open_changes(ctx)
    assert listed.id == "old" and listed.detected_at is None and listed.is_new is False
    assert build_digest(ctx).top_changes == []
    done = {**old, "id": "old-done", "status": "done"}
    store.execute("INSERT INTO changes VALUES (?, ?, ?, ?)", ["old-done", 2, "done", json.dumps(done)])
    assert [i.id for i in open_changes(ctx)] == ["old"]


def test_digest_ranks_and_suggests(ctx, store):
    certify_row(store)
    certify_row(store, "c2", "How many orders?")
    _save(item("d1", "new_data", ["r1"]), ctx)
    _save(item("d2", "definition", ["r1", "r2"], subject={"metric": "fraud_loss"}), ctx)
    _save(item("d3", "new_value", subject={"dimension": "orders.sales_channel", "value": "Marketplace"}), ctx)
    _save(item("d4", "question", ["r1"], subject={"certified_id": "c1"}), ctx)
    digest = build_digest(ctx)
    assert [c.kind for c in digest.top_changes] == ["definition", "question", "new_data", "new_value"]
    assert digest.until == T0 and digest.since == T0 - timedelta(days=7)
    first, second, third = digest.suggested_questions
    assert first.certified_id == "c1" and first.question == "Where is fraud loss concentrated?"
    assert second.question == "Fraud loss for Marketplace" and second.certified_id is None
    assert second.query == {"metrics": ["fraud_loss"],
                            "filters": [{"member": "orders.sales_channel", "op": "eq", "values": ["Marketplace"]}]}
    assert third.certified_id == "c2"
    assert len(build_digest(ctx, top=2, questions=1).top_changes) == 2
    assert len(build_digest(ctx, questions=1).suggested_questions) == 1


def test_digest_ties_rank_by_reach_then_order(ctx):
    _save(item("a", "new_data", ["r1"]), ctx)
    _save(item("b", "new_data", ["r1", "r2"]), ctx)
    _save(item("c", "new_data", ["r3"]), ctx)
    assert [c.id for c in build_digest(ctx).top_changes] == ["b", "a", "c"]


def test_digest_window_excludes_old_items(ctx, clock):
    _save(item("old", "new_data"), ctx)
    clock[0] = T0 + timedelta(days=7, seconds=1)
    _save(item("fresh", "new_value", subject={"dimension": "region", "value": "North"}), ctx)
    digest = build_digest(ctx)
    assert [c.id for c in digest.top_changes] == ["fresh"]
    assert [q.question for q in digest.suggested_questions] == ["Fraud loss for North"]


def test_digest_is_empty_without_changes(ctx):
    digest = build_digest(ctx)
    assert digest.top_changes == [] and digest.suggested_questions == []


def test_an_old_subject_with_a_bare_dimension_suggests_a_member_query(ctx):
    _save(item("old", "new_value", subject={"dimension": "region", "value": "North"}), ctx)
    [suggestion] = build_digest(ctx).suggested_questions
    assert suggestion.query["filters"] == [
        {"member": "dealers.region", "op": "eq", "values": ["North"]}]
