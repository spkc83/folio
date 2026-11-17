import json

import pytest

from living_reports.discovery.confirm import KIND_ORDER, confirm_proposals
from living_reports.discovery.run import discover, list_pending
from living_reports.semantic.kinds import FilterSpec

from .conftest import context, pid_of

N_PROPOSED = 1 + 2 + 24 + 1 + 4


def _probe_rows(store):
    return {pid: json.loads(payload)
            for pid, payload in store.rows("SELECT proposal_id, payload FROM discovery_probes")}


def test_discover_writes_proposals_and_probes(empty_store, fake_llm, draft):
    fake_llm.push(draft.model_dump())
    run = discover("test", context(empty_store))
    assert len(run.proposal_ids) == N_PROPOSED
    assert len(empty_store.graph.pending()) == N_PROPOSED
    probes = _probe_rows(empty_store)
    assert len(probes) == 5
    assert probes[pid_of(empty_store, "fraud_loss")]["value"] == "$7.5K"
    assert run.dropped == [] and run.skipped == []
    assert empty_store.graph.slice().node("orders") is None
    assert all(p.reason == f"discovery:{run.id}" for p in empty_store.graph.pending())
    assert all(p.proposed_by == "discovery" for p in empty_store.graph.pending())
    saved = empty_store.rows("SELECT id, source_id FROM discovery_runs")
    assert saved == [(run.id, "test")]


def test_discover_with_a_given_draft_makes_no_model_call(empty_store, fake_llm, draft):
    discover("test", context(empty_store), draft=draft)
    assert fake_llm.prompts == []


def test_metric_through_a_dropped_relationship_is_dropped(dup_store, draft):
    from living_reports.discovery.draft import DraftMetric

    draft.metrics.append(DraftMetric(
        name="loss_by_region_only", label="East loss", format="currency", agg="sum",
        field="orders.loss_amount",
        filter=FilterSpec(member="dealers.region", op="eq", values=["East"])))
    run = discover("test", context(dup_store), draft=draft)
    reasons = {d.name: d.reason for d in run.dropped}
    assert reasons["orders_dealer"] == "dealers.dealer_id has 1 duplicate keys"
    assert "No join path" in reasons["loss_by_region_only"]
    proposed = {p.change.id for p in dup_store.graph.pending()}
    assert {"fraud_loss", "fraud_rate", "order_count", "dealer_count"} <= proposed
    assert "orders_dealer" not in proposed and "loss_by_region_only" not in proposed
    dealers = next(p for p in dup_store.graph.pending() if p.change.id == "dealers")
    assert dealers.change.payload["unique_keys"] == [] and dealers.change.payload["primary_key"] == []


def test_rerun_skips_live_equal_nodes_and_pending_duplicates(empty_store, draft):
    ctx = context(empty_store)
    discover("test", ctx, draft=draft)
    pending = empty_store.graph.pending()
    base = [p.id for p in pending if p.change.kind in ("Source", "Dataset", "Field")]
    assert confirm_proposals(base, "omar", empty_store).failed == []
    empty_store.graph.reject(pid_of(empty_store, "orders_dealer"), "omar", "not now")
    run2 = discover("test", ctx, draft=draft)
    base_ids = {p.change.id for p in pending if p.change.kind in ("Source", "Dataset", "Field")}
    assert base_ids <= set(run2.skipped)
    assert {"fraud_loss", "fraud_rate", "order_count", "dealer_count"} <= set(run2.skipped)
    again = [p for p in empty_store.graph.pending() if p.change.id == "orders_dealer"]
    assert len(again) == 1 and again[0].reason == f"discovery:{run2.id}"
    assert len(empty_store.graph.pending()) == 1 + 4


def test_list_pending_views(empty_store, draft):
    discover("test", context(empty_store), draft=draft)
    views = list_pending(empty_store)
    order = [KIND_ORDER.index(v.kind) for v in views]
    assert order == sorted(order) and len(views) == N_PROPOSED
    by_id = {v.node_id: v for v in views}
    assert by_id["fraud_loss"].summary == "sum(orders.loss_amount)"
    assert by_id["fraud_loss"].probe.value == "$7.5K"
    assert by_id["order_count"].summary == "count(orders)"
    assert by_id["orders"].summary == "main.orders, key order_id"
    assert by_id["orders.sales_channel"].summary == "string, dimension"
    assert by_id["orders.order_created_date"].summary == "datetime, time"
    assert by_id["orders.loss_amount"].summary == "decimal"
    assert by_id["orders_dealer"].summary == "orders.dealer_id → dealers.dealer_id"
    assert by_id["test"].summary == "duckdb"
    assert by_id["orders"].probe is None and by_id["orders"].run_id is not None


def test_list_pending_ignores_other_proposals(store):
    from folio_graph import NodeChange

    payload = dict(store.graph.slice().node("fraud_loss").payload, label="Loss")
    store.graph.propose(NodeChange(id="fraud_loss", kind="MetricBinding", payload=payload),
                        "ana", "a person's edit")
    assert list_pending(store) == []


def test_discover_refreshes_a_cached_catalog_first(empty_store, draft):
    # PostgresSource caches its catalog; a discovery run must see new tables.
    from living_reports.sources.registry import source_for

    source = source_for("test")
    calls = []
    real = source.introspect
    source.refresh = lambda: calls.append("refresh")
    source.introspect = lambda: (calls.append("introspect"), real())[1]
    discover("test", context(empty_store), draft=draft)
    assert calls[:2] == ["refresh", "introspect"]


@pytest.fixture
def two_sources(store, monkeypatch, keyed_file):
    """The seeded demo model on duckdb_demo, and a second source ``test``."""
    from living_reports.sources.registry import reset_sources

    monkeypatch.setenv("LR_SOURCES", json.dumps({"test": {"kind": "duckdb", "path": str(keyed_file)}}))
    reset_sources()
    return store


def test_discovery_never_repoints_another_sources_datasets(two_sources, draft):
    store = two_sources
    run = discover("test", context(store), draft=draft)
    reasons = {d.name: d.reason for d in run.dropped}
    assert reasons["orders"] == "orders belongs to source duckdb_demo"
    assert reasons["dealers"] == "dealers belongs to source duckdb_demo"
    assert reasons["orders.loss_amount"] == "orders belongs to source duckdb_demo"
    assert reasons["orders_dealer"] == "orders belongs to source duckdb_demo"
    assert reasons["fraud_loss"] == "orders belongs to source duckdb_demo"
    assert reasons["dealer_count"] == "dealers belongs to source duckdb_demo"
    assert [p.change.id for p in store.graph.pending()] == ["test"]
    assert {d.source_id for d in store.graph.model().datasets} == {"duckdb_demo"}


def test_a_change_whose_live_node_has_another_kind_is_dropped(empty_store, draft):
    from living_reports.discovery.draft import DraftMetric

    discover("test", context(empty_store), draft=draft)
    confirm_proposals(None, "omar", empty_store)
    draft.metrics.append(DraftMetric(name="orders_dealer", label="X", format="count",
                                     agg="count", dataset="orders"))
    draft.relationships = []  # orders_dealer is live as a Relationship
    run = discover("test", context(empty_store), draft=draft)
    reasons = {d.name: d.reason for d in run.dropped}
    assert reasons["orders_dealer"] == "name collides with Relationship"
    assert empty_store.graph.pending() == []
