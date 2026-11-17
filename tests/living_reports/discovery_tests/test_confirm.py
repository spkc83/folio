import json

import pytest

from living_reports.discovery.confirm import certify_metric, confirm_proposals, rename_proposal
from living_reports.discovery.run import discover
from living_reports.engine.expressions import parse_expression

from .conftest import context, pid_of


@pytest.fixture
def run(empty_store, draft):
    return discover("test", context(empty_store), draft=draft)


def test_confirm_out_of_order_names_the_missing_endpoint(empty_store, run):
    pid = pid_of(empty_store, "fraud_loss")
    result = confirm_proposals([pid], "omar", empty_store)
    assert result.failed == [(pid, "Confirm orders.loss_amount first")]
    assert result.confirmed == []
    assert empty_store.graph.slice().node("fraud_loss") is None
    assert empty_store.graph.proposal(pid).status == "pending"


def test_confirm_a_field_before_its_dataset(empty_store, run):
    pid = pid_of(empty_store, "orders.loss_amount")
    assert confirm_proposals([pid], "omar", empty_store).failed == [(pid, "Confirm orders first")]


def test_confirm_all_orders_by_kind(empty_store, run):
    result = confirm_proposals(None, "omar", empty_store)
    assert result.failed == [] and len(result.confirmed) == len(run.proposal_ids)
    model = empty_store.graph.model()
    assert model.metric("fraud_loss").version == 1
    assert model.source == "test"
    assert len(empty_store.graph.slice().paths("orders", "dealers", ["joins_to"])) == 1
    assert empty_store.graph.pending() == []


def test_confirm_refuses_the_proposer(empty_store, run):
    pid = pid_of(empty_store, "test")
    result = confirm_proposals([pid], "discovery", empty_store)
    assert result.failed == [(pid, "The person who proposed a change cannot decide on it.")]


def test_rename_keeps_the_id(empty_store, run):
    old = pid_of(empty_store, "orders.sales_channel")
    p = rename_proposal(old, "Channel", "omar", empty_store)
    assert p.change.id == "orders.sales_channel" and p.change.payload["label"] == "Channel"
    assert p.reason.startswith(f"discovery:{run.id}") and "discovery" in p.reason.split(" ", 1)[1]
    assert p.proposed_by == "omar"
    closed = empty_store.graph.proposal(old)
    assert closed.status == "rejected" and closed.note == "renamed to Channel"
    assert closed.decided_by == "omar"


def test_rename_moves_the_probe(empty_store, run):
    old = pid_of(empty_store, "fraud_loss")
    new = rename_proposal(old, "Loss", "omar", empty_store).id
    ids = [r[0] for r in empty_store.rows("SELECT proposal_id FROM discovery_probes")]
    assert new in ids and old not in ids
    payload = json.loads(empty_store.row(
        "SELECT payload FROM discovery_probes WHERE proposal_id = ?", (new,))[0])
    assert payload["value"] == "$7.5K"


def test_rename_refuses_a_dataset(empty_store, run):
    with pytest.raises(ValueError, match="Only fields and metrics can be renamed"):
        rename_proposal(pid_of(empty_store, "orders"), "Orders", "omar", empty_store)


def test_rename_refuses_an_empty_label(empty_store, run):
    with pytest.raises(ValueError):
        rename_proposal(pid_of(empty_store, "fraud_loss"), "  ", "omar", empty_store)


def test_certify_expression_metric(empty_store, run):
    confirm_proposals(None, "omar", empty_store)
    model = empty_store.graph.model()
    expr = parse_expression("sum(orders.loss_amount)", "orders", model)
    empty_store.graph.put("net_expr", "MetricBinding", {
        "name": "net_expr", "label": "Net", "format": "currency", "certified": False,
        "expression": expr.model_dump(mode="json")}, by="import:omar")
    node = certify_metric("net_expr", "omar", empty_store)
    assert node.version == 2
    assert empty_store.graph.model().metric("net_expr").certified is True
    with pytest.raises(ValueError):
        certify_metric("net_expr", "omar", empty_store)
    with pytest.raises(ValueError):
        certify_metric("fraud_loss", "omar", empty_store)
    with pytest.raises(ValueError):
        certify_metric("ghost", "omar", empty_store)


def test_confirm_all_confirms_a_ratio_after_its_parts(empty_store, run):
    from folio_graph import NodeChange

    ratio = {"name": "aaa_loss_per_order", "label": "Loss per order", "format": "currency",
             "ratio": {"numerator": "fraud_loss", "denominator": "order_count"}}
    empty_store.graph.propose(NodeChange(id="aaa_loss_per_order", kind="MetricBinding",
                                         payload=ratio), "import:omar", "import:x")
    result = confirm_proposals(None, "omar", empty_store)
    assert result.failed == []
    assert empty_store.graph.model().metric("aaa_loss_per_order").ratio is not None


def test_the_renamer_cannot_confirm_the_renamed_proposal(empty_store, run):
    # Four eyes: whoever changed the content is its proposer now.
    new = rename_proposal(pid_of(empty_store, "orders.sales_channel"), "Channel", "omar",
                          empty_store)
    result = confirm_proposals([new.id], "omar", empty_store)
    assert result.failed == [(new.id, "The person who proposed a change cannot decide on it.")]
    assert empty_store.graph.proposal(new.id).status == "pending"
    from living_reports.discovery.run import list_pending

    view = next(v for v in list_pending(empty_store) if v.node_id == "orders.sales_channel")
    assert view.run_id == run.id


def _definition_change(store):
    """A person's proposal on a live metric: not a model proposal."""
    from folio_graph import NodeChange

    payload = dict(store.graph.slice().node("fraud_loss").payload, label="Loss")
    return store.graph.propose(NodeChange(id="fraud_loss", kind="MetricBinding", payload=payload),
                               "ana", "Exclude friendly fraud.")


def test_confirm_refuses_a_proposal_that_is_not_a_model_proposal(store):
    p = _definition_change(store)
    assert confirm_proposals([p.id], "omar", store).failed == [(p.id, "Not a model proposal")]
    assert store.graph.proposal(p.id).status == "pending"


def test_rename_refuses_a_proposal_that_is_not_a_model_proposal(store):
    p = _definition_change(store)
    with pytest.raises(ValueError, match="Not a model proposal"):
        rename_proposal(p.id, "Loss", "omar", store)
    assert store.graph.proposal(p.id).status == "pending"


def test_a_confirmed_change_to_a_live_node_runs_the_graph_follow_up(empty_store, run, draft,
                                                                    monkeypatch):
    from living_reports.discovery.run import discover
    from living_reports.reports import changes

    confirm_proposals(None, "omar", empty_store)
    draft.metrics[0].label = "Total fraud loss"  # fraud_loss, now a change to a live node
    discover("test", context(empty_store), draft=draft)
    seen = []
    monkeypatch.setattr(changes, "apply_confirmed",
                        lambda p, ctx, refresh: seen.append((p.id, ctx.source_id, refresh)))
    pid = pid_of(empty_store, "fraud_loss")
    assert confirm_proposals([pid], "omar", empty_store).failed == []
    assert seen == [(pid, "test", True)]
    assert empty_store.graph.model().metric("fraud_loss").label == "Total fraud loss"


def test_a_new_node_needs_no_report_refresh(empty_store, run, monkeypatch):
    from living_reports.reports import changes

    seen = []
    monkeypatch.setattr(changes, "apply_confirmed", lambda p, ctx, refresh: seen.append(refresh))
    confirm_proposals(None, "omar", empty_store)
    assert seen and not any(seen)


def test_a_follow_up_without_a_source_is_skipped_not_an_error(empty_store, run, monkeypatch):
    from living_reports.discovery import confirm as confirm_module
    from living_reports.sources import SourceError

    def no_source(p, by, store):
        raise SourceError("The model names no single source; give a source id")

    monkeypatch.setattr(confirm_module, "_context", no_source)
    result = confirm_proposals(None, "omar", empty_store)
    assert result.failed == [] and empty_store.graph.pending() == []


def test_rename_changes_nothing_but_the_label(empty_store, run):
    for node_id in ("orders.sales_channel", "fraud_loss"):
        old = empty_store.graph.proposal(pid_of(empty_store, node_id))
        new = rename_proposal(old.id, "New name", "omar", empty_store)
        assert new.change.id == old.change.id and new.change.kind == old.change.kind
        assert {k: v for k, v in new.change.payload.items() if k != "label"} == {
            k: v for k, v in old.change.payload.items() if k != "label"}
        assert new.change.payload["label"] == "New name"
        assert "renamed by omar" in new.reason and old.proposed_by in new.reason


def test_a_second_rename_rebuilds_the_reason(empty_store, run):
    first = rename_proposal(pid_of(empty_store, "orders.sales_channel"), "A", "omar", empty_store)
    second = rename_proposal(first.id, "B", "chidi", empty_store)
    assert second.reason == f"discovery:{run.id} (renamed by chidi; proposed by discovery)"
    assert second.proposed_by == "chidi"


def _import_as_omar(store):
    """Proposals from an import by omar: omar is the proposer."""
    from living_reports.discovery.preview import model_from_changes  # noqa: F401
    from living_reports.discovery.propose import to_changes
    from living_reports.discovery.run import propose_changes
    from living_reports.semantic.kinds import SourcePayload

    from .conftest import make_catalog, make_draft

    changes, _ = to_changes(make_draft(), make_catalog(), SourcePayload(name="test"))
    return propose_changes(changes, "test", context(store), by="omar", reason_prefix="import",
                           note="by omar")


def test_the_importer_cannot_confirm_after_someone_else_renames(empty_store):
    _import_as_omar(empty_store)
    old = pid_of(empty_store, "orders.sales_channel")
    new = rename_proposal(old, "Channel", "ana", empty_store)
    assert new.proposed_by == "ana"
    result = confirm_proposals([new.id], "omar", empty_store)
    assert result.failed == [(new.id, "The person who proposed a change cannot decide on it.")]
    assert empty_store.graph.proposal(new.id).status == "pending"
    # ana renamed it, so ana cannot confirm it either; a third person can.
    assert confirm_proposals([new.id], "ana", empty_store).failed[0][1].startswith("The person")


def test_the_lineage_is_structured_not_parsed_from_the_reason(empty_store):
    from living_reports.discovery.confirm import proposers_of

    _import_as_omar(empty_store)
    old = pid_of(empty_store, "orders.sales_channel")
    new = rename_proposal(old, "Channel", "ana", empty_store)
    assert proposers_of(empty_store, new) == ["omar", "ana"]
    # A reason edited to name no one changes nothing.
    empty_store.execute("UPDATE proposals SET body = json_set(body, '$.reason', 'import:x') "
                        "WHERE id = ?", (new.id,))
    assert empty_store.graph.proposal(new.id).reason == "import:x"
    assert proposers_of(empty_store, empty_store.graph.proposal(new.id)) == ["omar", "ana"]


def test_plain_confirms_still_work_with_a_lineage(empty_store, run):
    new = rename_proposal(pid_of(empty_store, "orders.sales_channel"), "Channel", "ana",
                          empty_store)
    confirm_proposals(None, "omar", empty_store)  # discovery, then ana: omar may decide
    assert empty_store.graph.proposal(new.id).status == "confirmed"
