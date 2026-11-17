from living_reports.semantic.demo import DEMO_START, seed_demo


def test_seed_is_idempotent(fg):
    seed_demo(fg)
    before = fg.version()
    seed_demo(fg)
    assert fg.version() == before


def test_seed_valid_from_is_demo_start(fg):
    seed_demo(fg)
    assert fg.slice().node("fraud_loss").valid_from == DEMO_START


def test_demo_model_has_two_datasets_and_a_join(fg):
    seed_demo(fg)
    m = fg.model()
    assert sorted(d.name for d in m.datasets) == ["dealers", "orders"]
    assert [r.name for r in m.relationships] == ["orders_dealer"]
    rel = m.relationships[0]
    assert (rel.from_dataset, rel.to_dataset, rel.from_columns, rel.to_columns) == (
        "orders", "dealers", ["dealer_id"], ["dealer_id"])
    assert "dealers.region" in m.members and "orders.region" not in m.members
    assert m.field("dealers.region").label == "Region"
    assert m.metric("dealer_count").measure.agg == "count_distinct"
    assert m.metric("dealer_count").label == "Dealers"
    assert m.dataset("dealers").table == "main.dealers"
    assert m.dataset("orders").time_field == "order_created_date"
    assert m.field("orders.fraud_type").synonyms == ["fraud category"]


def test_demo_vocabulary_lists_member_dimensions(fg):
    seed_demo(fg)
    v = fg.vocabulary()
    dims = {t.id for t in v.dimensions}
    assert {"orders.sales_channel", "dealers.region", "orders.fraud_type",
            "orders.payment_method", "orders.tenure_band"} <= dims
    assert v.time_fields == ["orders.order_created_date"]
