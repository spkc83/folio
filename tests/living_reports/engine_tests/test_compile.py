from datetime import date

import pytest

from living_reports.engine import Filter, FilterGroup, Having, OutOfScope, Query, TimeRange
from living_reports.engine.compile import compile, row_count_query, sqlglot_schema

from .conftest import payload, replace_dataset, run
from living_reports.semantic.kinds import Measure


def spec_query(**update) -> Query:
    body = {
        "metrics": ["fraud_loss"],
        "dimensions": ["orders.sales_channel"],
        "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}],
        "time": {"field": "orders.order_created_date", "start": "2025-01-01", "end": "2026-01-01"},
        "order": [["fraud_loss", "desc"]],
    }
    body.update(update)
    return Query.model_validate(body)


def east_2025(**update) -> Query:
    return spec_query(order=[], **update)


def test_spec_5_3_shape_and_rows(model, duck):
    c = compile(spec_query(), model)
    assert "LEFT JOIN main.dealers AS dealers ON orders.dealer_id = dealers.dealer_id" in c.sql
    assert "WHERE dealers.region = ?" in c.sql
    assert "GROUP BY orders.sales_channel" in c.sql
    assert 'ORDER BY "fraud_loss" DESC' in c.sql
    assert c.params == ["East", date(2025, 1, 1), date(2026, 1, 1)]
    assert run(c, duck) == [("App", 150.0), ("Web", 30.0)]
    assert c.plan.joins == {"orders": ["orders_dealer"]}
    assert c.plan.group_columns == ["orders.sales_channel"]
    assert c.plan.base_datasets == ["orders"]
    assert c.plan.time_field == "orders.order_created_date"
    assert c.plan.dialect == "duckdb"


def test_filter_values_are_placeholders(model, duck):
    bad = "x'; drop table orders;--"
    q = spec_query(filters=[{"member": "dealers.region", "op": "eq", "values": [bad]}])
    c = compile(q, model)
    assert "drop" not in c.sql.lower()
    assert bad in c.params
    assert run(c, duck) == []


def test_measure_filter_is_a_placeholder(model, duck):
    c = compile(east_2025(metrics=["net_fraud_loss"]), model)
    assert "FILTER(WHERE orders.fraud_type <> ?)" in c.sql
    assert "friendly_fraud" in c.params
    assert c.params[0] == "friendly_fraud"
    assert run(c, duck) == [("App", 100.0), ("Web", 30.0)]


def test_count_and_avg_and_ratio(model, duck):
    c = compile(Query(metrics=["order_count", "fraud_rate", "loss_per_order"]), model)
    assert run(c, duck) == [(8, 0.75, 38.75)]
    assert "COUNT(*)" in c.sql and "NULLIF" in c.sql
    assert c.plan.metric_versions == {"order_count": 1, "fraud_rate": 1, "loss_per_order": 1,
                                      "fraud_loss": 1}


def test_default_order_and_limit(model, duck):
    c = compile(Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"], limit=1), model)
    assert run(c, duck) == [("App", 170.0)]
    full = compile(Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"]), model)
    assert run(full, duck) == [("App", 170.0), ("Web", 100.0), ("Telesales", 40.0)]


def _one(model, duck, metric, *filters):
    c = compile(Query(metrics=[metric], filters=list(filters)), model)
    return run(c, duck)[0][0]


def test_filter_ops(model, duck):
    c = compile(Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"],
                      filters=[Filter(member="orders.sales_channel", op="in",
                                      values=["App", "Web"])]), model)
    assert len(run(c, duck)) == 2
    assert _one(model, duck, "order_count",
                Filter(member="orders.order_value", op="between", values=[200, 400])) == 5
    assert _one(model, duck, "order_count",
                Filter(member="orders.fraud_type", op="is_null")) == 0
    assert _one(model, duck, "fraud_loss",
                Filter(member="orders.fraud_type", op="contains", values=["fraud"])) == 90.0
    group = FilterGroup(**{"or": [
        Filter(member="orders.sales_channel", op="eq", values=["Telesales"]),
        Filter(member="dealers.region", op="eq", values=["East"]),
    ]})
    assert _one(model, duck, "order_count", group) == 7


def test_more_filter_ops(model, duck):
    assert _one(model, duck, "order_count",
                Filter(member="orders.sales_channel", op="not_in", values=["App"])) == 4
    assert _one(model, duck, "order_count",
                Filter(member="orders.order_value", op="gte", values=[350])) == 3
    assert _one(model, duck, "order_count",
                Filter(member="orders.order_value", op="lt", values=[200])) == 2
    assert _one(model, duck, "order_count",
                Filter(member="orders.order_value", op="lte", values=[200])) == 3
    assert _one(model, duck, "order_count",
                Filter(member="orders.sales_channel", op="ne", values=["App"])) == 4
    nested = FilterGroup(**{"and": [
        Filter(member="dealers.region", op="eq", values=["East"]),
        FilterGroup(**{"or": [Filter(member="orders.sales_channel", op="eq", values=["Web"]),
                              Filter(member="orders.order_value", op="gt", values=[400])]}),
    ]})
    assert _one(model, duck, "order_count", nested) == 3  # O1 (500), O3 (Web), O7 (Web)


def test_contains_wraps_the_value_in_params(model):
    c = compile(Query(metrics=["fraud_loss"], filters=[
        Filter(member="orders.fraud_type", op="contains", values=["fraud"])]), model)
    assert "%fraud%" in c.params
    assert "%" not in c.sql.replace("%s", "")


def test_having(model, duck):
    q = Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"],
              having=[Having(metric="fraud_loss", op="gt", values=[100])])
    c = compile(q, model)
    assert "HAVING" in c.sql and 100 in c.params
    assert run(c, duck) == [("App", 170.0)]


def test_time_defaults_to_the_base_time_field(model, duck):
    c = compile(Query(metrics=["order_count"], time=TimeRange(start=date(2025, 1, 1))), model)
    assert c.plan.time_field == "orders.order_created_date"
    assert run(c, duck) == [(7,)]


def test_time_without_a_time_field_is_out_of_scope(model):
    with pytest.raises(OutOfScope, match="dealers has no time field"):
        compile(Query(metrics=["dealer_count"], time=TimeRange(start=date(2025, 1, 1))), model)


def test_unknown_ids_are_out_of_scope(model):
    with pytest.raises(OutOfScope) as err:
        compile(Query(metrics=["profit"]), model)
    assert err.value.unknown == ["profit"]
    assert err.value.reason == "The model has no entry for: profit"
    with pytest.raises(OutOfScope) as err:
        compile(Query(metrics=["fraud_loss"], dimensions=["orders.store"]), model)
    assert err.value.unknown == ["orders.store"]
    with pytest.raises(OutOfScope) as err:
        compile(Query(metrics=["fraud_loss"],
                      filters=[Filter(member="orders.nope", op="eq", values=[1])]), model)
    assert err.value.unknown == ["orders.nope"]


def test_computed_field_is_spliced(model, duck):
    c = compile(Query(metrics=["fraud_loss_k"]), model)
    assert round(run(c, duck)[0][0], 2) == 0.31
    assert "orders.loss_amount / 1000" in c.sql


def test_row_count_query(model, duck):
    c = row_count_query(spec_query(), model)
    assert c.sql.startswith("SELECT COUNT(*)")
    assert "GROUP BY" not in c.sql
    assert run(c, duck) == [(5,)]


def test_postgres_rendering_has_no_dialect_code(model):
    duck_c = compile(spec_query(order=[]), model)
    pg = compile(spec_query(order=[]), model, "postgres")
    assert "%s" in pg.sql and "NULLS LAST" in pg.sql
    assert pg.params == duck_c.params
    assert pg.plan.dialect == "postgres"


def test_hash_is_stable_and_version_sensitive(model):
    a, b = compile(spec_query(), model), compile(spec_query(), model)
    assert a.hash == b.hash
    v2 = model.with_metric("fraud_loss", payload(
        "fraud_loss", "currency", measure=Measure(agg="sum", field="orders.loss_amount")),
        version=2)
    c = compile(spec_query(), v2)
    assert c.hash != a.hash
    assert c.plan.metric_versions["fraud_loss"] == 2
    no_join = compile(Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"]), model)
    assert no_join.plan.joins == {"orders": []}


def test_hash_ignores_the_model_version(model):
    later = model.model_copy(update={"model_version": 99})
    a, b = compile(spec_query(), model), compile(spec_query(), later)
    assert a.hash == b.hash
    assert b.plan.model_version == 99


def test_order_by_a_dimension(model, duck):
    c = compile(Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"],
                      order=[["orders.sales_channel", "asc"]]), model)
    assert [r[0] for r in run(c, duck)] == ["App", "Telesales", "Web"]


def test_filter_on_joined_dataset_from_a_filterless_metric(model):
    with pytest.raises(OutOfScope) as err:
        compile(Query(metrics=["dealer_count"], filters=[
            Filter(member="orders.sales_channel", op="eq", values=["App"])]), model)
    assert err.value.reason == (
        "Dealer count cannot be broken down or filtered by Sales channel: "
        "dealers has no link to orders")
    assert "No join path from dealers to orders within 2 hops" in err.value.detail


def test_two_bases_with_a_dimension_one_cannot_reach(model):
    with pytest.raises(OutOfScope) as err:
        compile(Query(metrics=["fraud_loss", "dealer_count"],
                      dimensions=["orders.sales_channel"]), model)
    assert err.value.reason.startswith("Dealer count cannot be broken down or filtered by")
    assert "No join path" in err.value.detail


def test_dimension_on_the_joined_dataset(model, duck):
    c = compile(Query(metrics=["fraud_loss"], dimensions=["dealers.region"]), model)
    assert run(c, duck) == [("East", 200.0), ("West", 110.0)]


def test_sqlglot_schema(model):
    schema = sqlglot_schema(model)
    assert schema["main"]["orders"]["loss_amount"] == "DOUBLE"
    assert schema["main"]["orders"]["order_created_date"] == "DATE"
    assert schema["main"]["dealers"] == {"dealer_id": "TEXT", "region": "TEXT"}
    assert "loss_k" not in schema["main"]["orders"]


def test_table_without_a_schema_is_in_main(model):
    m = replace_dataset(model, "dealers", table="dealers")
    assert "dealers" in sqlglot_schema(m)["main"]


def test_measure_sql_renders_a_definition(model):
    from living_reports.engine.compile import measure_sql

    m = model.metric("net_fraud_loss").measure
    assert measure_sql(m, model, "duckdb") == (
        "SUM(orders.loss_amount) FILTER(WHERE orders.fraud_type <> 'friendly_fraud')")
    assert measure_sql(model.metric("order_count").measure, model, "duckdb") == "COUNT(*)"
    assert measure_sql(model.metric("dealer_count").measure, model, "postgres") == (
        "COUNT(DISTINCT dealers.dealer_id)")
    assert measure_sql(model.metric("fraud_loss_k").measure, model, "duckdb") == (
        "SUM(orders.loss_amount / 1000)")


def test_a_ratio_cycle_is_out_of_scope(model):
    from living_reports.semantic.kinds import Ratio

    cyclic = model.with_metric("fraud_loss", payload(
        "fraud_loss", ratio=Ratio(numerator="fraud_loss", denominator="order_count")))
    with pytest.raises(OutOfScope, match="refers to itself"):
        compile(Query(metrics=["fraud_loss"]), cyclic)


def test_contains_escapes_like_wildcards(model, duck):
    duck.execute("INSERT INTO main.orders VALUES ('O9', '2025-08-15', 'App', 'D1', 5, 1, "
                 "'100%_off', 10), ('O10', '2025-08-15', 'App', 'D1', 7, 1, '100xxoff', 10)")
    c = compile(Query(metrics=["fraud_loss"], filters=[
        Filter(member="orders.fraud_type", op="contains", values=["0%_o"])]), model)
    assert "ESCAPE" in c.sql
    assert run(c, duck) == [(5.0,)]


def test_placeholder_count_matches_params(model, monkeypatch):
    from living_reports.engine import compile as compile_module

    real = compile_module._placeholder

    def unvalued(v):
        node = real(v)
        node.meta.pop("value")
        return node

    monkeypatch.setattr(compile_module, "_placeholder", unvalued)
    with pytest.raises(AssertionError, match="placeholder"):
        compile(spec_query(), model)
