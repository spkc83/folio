import pytest

from folio_graph import NodeChange
from living_reports.discovery.draft import DraftMetric
from living_reports.discovery.preview import model_from_changes
from living_reports.discovery.probe import probe_changes, probe_metric, probe_relationship
from living_reports.discovery.propose import to_changes, with_relationship_keys
from living_reports.semantic.kinds import FilterSpec, RelationshipPayload, SourcePayload

REL = RelationshipPayload(name="orders_dealer", from_dataset="orders", to_dataset="dealers",
                          from_columns=["dealer_id"], to_columns=["dealer_id"])


def _model(draft, src):
    changes, _ = to_changes(draft, src.introspect(), SourcePayload(name="test"))
    changes = with_relationship_keys(changes)
    return changes, model_from_changes(changes)


def test_unique_one_side_passes(draft, source):
    _, m = _model(draft, source)
    p = probe_relationship(REL, m, source)
    assert p.status == "pass" and p.kind == "uniqueness" and p.target == "orders_dealer"
    assert p.detail == "dealers.dealer_id is unique over 7 rows"
    assert "COUNT(DISTINCT" in p.sql


def test_duplicate_one_side_fails_the_probe(draft, dup_source):
    _, m = _model(draft, dup_source)
    p = probe_relationship(REL, m, dup_source)
    assert p.status == "fail"
    assert p.detail == "dealers.dealer_id has 1 duplicate keys"


def test_metric_probe_runs_once_with_a_limit(draft, source, monkeypatch):
    _, m = _model(draft, source)
    seen = []
    real = source.run

    def spy(sql, params=(), **kw):
        seen.append(kw)
        return real(sql, params, **kw)

    monkeypatch.setattr(source, "run", spy)
    p = probe_metric("order_count", m, source)
    assert (p.status, p.value, p.detail) == ("pass", "3,000", "Orders: 3,000")
    assert seen == [{"limit": 1, "timeout_s": 20}]
    assert p.sql.startswith("SELECT")


def test_metric_probe_values(draft, source):
    _, m = _model(draft, source)
    assert probe_metric("fraud_loss", m, source).value == "$7.5K"
    assert probe_metric("dealer_count", m, source).value == "7"


def test_metric_probe_fails_on_compile(draft, source):
    _, m = _model(draft, source)
    p = probe_metric("ghost", m, source)
    assert p.status == "fail" and p.sql == ""
    assert "The model has no entry for" in p.detail


def test_metric_probe_fails_on_a_source_error(draft, source):
    draft.metrics.append(DraftMetric(name="channel_sum", label="Channel sum", format="number",
                                     agg="sum", field="orders.sales_channel"))
    _, m = _model(draft, source)
    p = probe_metric("channel_sum", m, source)
    assert p.status == "fail" and p.sql


def _region_metric():
    return DraftMetric(name="loss_by_region_only", label="East loss", format="currency",
                       agg="sum", field="orders.loss_amount",
                       filter=FilterSpec(member="dealers.region", op="eq", values=["East"]))


def test_probe_changes_on_good_data_joins(draft, source):
    draft.metrics.append(_region_metric())
    changes, m = _model(draft, source)
    probes = probe_changes(changes, m, source)
    assert probes["orders_dealer"].status == "pass"
    assert probes["loss_by_region_only"].status == "pass"
    assert set(probes) == {"orders_dealer", "fraud_loss", "fraud_rate", "order_count",
                           "dealer_count", "loss_by_region_only"}


def test_probe_changes_drops_metrics_behind_a_failed_relationship(draft, dup_source):
    draft.metrics.append(_region_metric())
    changes, m = _model(draft, dup_source)
    probes = probe_changes(changes, m, dup_source)
    assert probes["orders_dealer"].status == "fail"
    assert probes["dealer_count"].status == "pass"
    assert probes["loss_by_region_only"].status == "fail"
    assert "No join path" in probes["loss_by_region_only"].detail, probes["loss_by_region_only"].detail
    assert not any(isinstance(c, NodeChange) and c.kind == "Field" and c.id in probes
                   for c in changes)


def test_a_composite_key_probe(draft, source):
    _, m = _model(draft, source)
    rel = RelationshipPayload(name="pair", from_dataset="orders", to_dataset="dealers",
                              from_columns=["dealer_id", "dealer_id"],
                              to_columns=["dealer_id", "region"])
    p = probe_relationship(rel, m, source)
    assert p.status == "pass", p.detail
    assert p.detail == "dealers.dealer_id,region is unique over 7 rows"


@pytest.mark.parametrize("bad", ["main.dealers; DROP TABLE x", "read_csv('x')"])
def test_probe_sql_goes_through_the_read_only_check(draft, source, bad):
    _, m = _model(draft, source)
    ds = m.dataset("dealers").model_copy(update={"table": bad})
    m2 = m.model_copy(update={"datasets": [ds, m.dataset("orders")]})
    p = probe_relationship(REL, m2, source)
    assert p.status == "fail"
