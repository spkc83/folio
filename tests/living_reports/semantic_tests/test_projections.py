from datetime import datetime

import pytest

from folio_graph import GraphError
from living_reports.semantic.demo import seed_demo
from living_reports.semantic.kinds import FilterSpec, Measure

from .conftest import fraud_loss

FRIENDLY = FilterSpec(member="orders.fraud_type", op="ne", values=["friendly_fraud"])


@pytest.fixture
def seeded(fg):
    seed_demo(fg)
    return fg


def _v2(fg, clock):
    clock.set(datetime(2025, 7, 20))
    fg.put("fraud_loss", "MetricBinding", fraud_loss(filter=FRIENDLY),
           valid_from=datetime(2025, 7, 1))


def test_semantic_model_matches_seed(seeded):
    m = seeded.model()
    assert sorted(d.name for d in m.datasets) == ["dealers", "orders"]
    assert m.source == "duckdb_demo"
    assert m.name == "duckdb_demo"
    assert m.dialect == "duckdb"
    assert m.metric("fraud_loss").measure == Measure(agg="sum", field="orders.loss_amount")
    assert m.field("dealers.region").dimension is not None
    assert m.dataset("orders").time_field == "order_created_date"
    assert m.metric_versions == {
        "dealer_count": 1, "fraud_loss": 1, "fraud_rate": 1, "order_count": 1, "order_value": 1,
    }
    with pytest.raises(KeyError):
        m.metric("nope")


def test_model_version_is_the_slice_stamp(seeded, clock):
    m = seeded.model()
    assert m.model_version == seeded.version()
    _v2(seeded, clock)
    assert seeded.model().model_version > m.model_version
    assert seeded.model(known_at=datetime(2025, 7, 10)).model_version == m.model_version


def test_model_as_of_reads_the_old_definition(seeded, clock):
    _v2(seeded, clock)
    old = seeded.model(valid_at=datetime(2025, 6, 15), known_at=datetime(2025, 7, 21))
    assert old.metric("fraud_loss").measure.filter is None
    assert seeded.model().metric("fraud_loss").version == 2
    assert seeded.model().metric("fraud_loss").measure.filter == FRIENDLY


def test_with_metric_is_a_pure_preview(seeded):
    m = seeded.model()
    m2 = m.with_metric("fraud_loss", fraud_loss(filter=FRIENDLY))
    assert m2.metric("fraud_loss").version == 2
    assert m2.metric("fraud_loss").measure.filter == FRIENDLY
    assert seeded.model().metric("fraud_loss").version == 1
    assert m.metric("fraud_loss").version == 1


def test_vocabulary_lists_terms(seeded):
    v = seeded.vocabulary()
    assert sorted(t.id for t in v.metrics) == [
        "dealer_count", "fraud_loss", "fraud_rate", "order_count", "order_value"]
    assert "orders.sales_channel" in [t.id for t in v.dimensions]
    assert v.time_fields == ["orders.order_created_date"]
    assert v.label("orders.sales_channel") == "Sales channel"
    assert v.label("fraud_loss") == "Fraud loss"
    assert v.label("unknown.member") == "unknown.member"
    assert v.is_time("orders.order_created_date")
    assert "between" in v.operators
    assert v.grains == ["day", "week", "month", "quarter", "year"]
    assert v.model_version == seeded.version()


def test_binding_to_retired_field_is_an_error(seeded, clock):
    clock.set(datetime(2024, 3, 1))
    seeded.graph.retire("orders.loss_amount", "omar")
    with pytest.raises(GraphError) as err:
        seeded.model()
    assert "fraud_loss" in str(err.value)
    assert "orders.loss_amount" in str(err.value)


def test_model_before_seed_is_empty(seeded):
    m = seeded.model(known_at=datetime(2023, 1, 1))
    assert m.datasets == []
    assert m.metrics == []
    assert m.model_version == 0


def test_content_hash_ignores_the_stamp_but_not_definitions(seeded, clock):
    from living_reports.semantic.kinds import ReportPayload

    before = seeded.model().content_hash
    clock.set(datetime(2024, 2, 1))
    seeded.put("report:r1", "Report", ReportPayload(title="R", tier="team", author="ana"))
    assert seeded.model().content_hash == before
    _v2(seeded, clock)
    assert seeded.model().content_hash != before
