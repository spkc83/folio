from datetime import datetime

import pytest

from folio_graph import GraphError, NodeChange
from living_reports.discovery.preview import model_from_changes
from living_reports.discovery.propose import to_changes
from living_reports.semantic.kinds import Measure, SourcePayload


def test_model_from_changes_matches_the_draft(catalog, draft):
    changes, _ = to_changes(draft, catalog, SourcePayload(name="test"))
    m = model_from_changes(changes)
    assert [d.name for d in m.datasets] == ["dealers", "orders"]
    assert m.source == "test" and m.name == "test"
    assert m.metric("fraud_loss").measure == Measure(agg="sum", field="orders.loss_amount")
    assert m.field("orders.sales_channel").dimension is not None
    assert m.relationships[0].name == "orders_dealer"
    assert m.model_version == 0 and m.metric("fraud_loss").version == 0
    assert isinstance(m.valid_at, datetime)


def test_preview_merges_over_known(fg, store):
    known = fg.model()
    payload = dict(fg.slice().node("fraud_loss").payload)
    payload["measure"] = {**payload["measure"], "filter": {
        "member": "orders.fraud_type", "op": "ne", "values": ["friendly_fraud"]}}
    change = NodeChange(id="fraud_loss", kind="MetricBinding", payload=payload)
    m = model_from_changes([change], known)
    assert m.metric("fraud_loss").measure.filter is not None
    assert {x.id for x in m.metrics} == {x.id for x in known.metrics}
    assert [d.name for d in m.datasets] == [d.name for d in known.datasets]
    assert m.source == known.source


def test_missing_field_is_a_graph_error(catalog, draft):
    changes, _ = to_changes(draft, catalog, SourcePayload(name="test"))
    metric = NodeChange(id="ghost_sum", kind="MetricBinding", payload={
        "name": "ghost_sum", "label": "Ghost", "format": "number",
        "measure": {"agg": "sum", "field": "orders.ghost"}})
    with pytest.raises(GraphError, match="orders.ghost"):
        model_from_changes([*changes, metric])
