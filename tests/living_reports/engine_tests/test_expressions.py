import json

import pytest
from pydantic import ValidationError

from living_reports.engine import Query, QueryRejected
from living_reports.engine.compile import compile
from living_reports.engine.expressions import parse_expression, render, validate_expression
from living_reports.semantic.kinds import Expression, MetricBindingPayload

from .conftest import make_field, run

NET = "sum(orders.loss_amount) filter (where orders.fraud_type <> 'friendly_fraud')"


def _net_model(model):
    e = parse_expression(NET, "orders", model)
    payload = MetricBindingPayload(name="net_expr", label="Net", format="currency", expression=e)
    return model.with_metric("net_expr", payload)


def test_parse_stores_an_ast_and_member_ids(model):
    e = parse_expression(NET, "orders", model)
    assert e.fields == ["orders.fraud_type", "orders.loss_amount"]
    assert e.dataset == "orders" and e.dialect == "duckdb" and e.sql == NET
    assert isinstance(e.ast, list | dict)
    json.dumps(e.ast)
    validate_expression(e, model)


def test_expression_metric_matches_typed_measure(model, duck):
    m = _net_model(model)
    c = compile(Query(metrics=["net_expr", "net_fraud_loss"], dimensions=["orders.sales_channel"]), m)
    rows = run(c, duck)
    assert rows and all(r[1] == r[2] for r in rows)
    assert c.plan.member_versions.keys() >= {"orders.fraud_type", "orders.loss_amount"}


def test_expression_renders_for_postgres(model):
    c = compile(Query(metrics=["net_expr"]), _net_model(model), "postgres")
    assert "FILTER(WHERE orders.fraud_type <> 'friendly_fraud')" in c.sql


def test_expression_uses_member_ids_not_columns(model):
    orders = model.dataset("orders")
    chargeback = make_field("orders", "chargeback", "integer", column="chargeback_within_60d")
    datasets = [d.model_copy(update={"fields": [*d.fields, chargeback]}) if d.name == "orders"
                else d for d in model.datasets]
    m = model.model_copy(update={"datasets": datasets})
    assert orders.name == "orders"
    e = parse_expression("sum(orders.chargeback)", "orders", m)
    assert render(e, m, lambda ds: ds).sql(dialect="duckdb") == "SUM(orders.chargeback_within_60d)"
    with pytest.raises(QueryRejected, match="orders.chargeback_within_60d"):
        parse_expression("sum(orders.chargeback_within_60d)", "orders", m)


def test_computed_field_splices_into_an_expression(model):
    e = parse_expression("max(orders.loss_k)", "orders", model)
    assert render(e, model, lambda ds: ds).sql() == "MAX(orders.loss_amount / 1000)"


@pytest.mark.parametrize("sql", [
    "sum((select 1))",
    "sum(orders.loss_amount) + count(dealers.dealer_id)",
    "orders.loss_amount * 2",
    "sum(orders.loss_amount) / count(*)",
    "sum(sum(orders.loss_amount))",
    "sum(orders.loss_amount) filter (where count(*) > 1)",
    "max(getenv('HOME'))",
    "sum(dealers.region)",
    "sum(main.orders.loss_amount)",
    "sum(orders.loss_amount); drop table orders",
    "sum(",
])
def test_rejections(model, sql):
    with pytest.raises(QueryRejected):
        parse_expression(sql, "orders", model)


def test_bare_column_names_the_member_rule(model):
    with pytest.raises(QueryRejected, match="use the member id"):
        parse_expression("sum(loss_amount)", "orders", model)


def test_unknown_dataset_is_rejected(model):
    with pytest.raises(QueryRejected, match="nope"):
        parse_expression("count(*)", "nope", model)


def _bad(**update):
    body = {"sql": "count(*)", "dialect": "duckdb", "dataset": "orders", "fields": [],
            "ast": {"class": "nope"}}
    body.update(update)
    return Expression(**body)


def test_payload_validator_rejects_a_bad_ast():
    with pytest.raises(ValidationError):
        MetricBindingPayload(name="x", label="X", format="count", expression=_bad())


def test_payload_validator_rejects_text_that_differs_from_the_ast(model):
    e = parse_expression("sum(orders.loss_amount)", "orders", model)
    with pytest.raises(ValidationError, match="does not match"):
        MetricBindingPayload(name="x", label="X", format="count",
                             expression=e.model_copy(update={"sql": "count(*)"}))


def test_payload_validator_rejects_wrong_fields(model):
    e = parse_expression("sum(orders.loss_amount)", "orders", model)
    with pytest.raises(ValidationError, match="fields"):
        MetricBindingPayload(name="x", label="X", format="count",
                             expression=e.model_copy(update={"fields": ["orders.order_id"]}))


def test_payload_validator_accepts_a_parsed_expression(model):
    e = parse_expression(NET, "orders", model)
    p = MetricBindingPayload(name="x", label="X", format="currency", expression=e)
    assert MetricBindingPayload(**p.model_dump(mode="json")).expression.fields == e.fields


def test_dependents_reach_every_expression_field(fg):
    from living_reports.semantic.demo import seed_demo

    seed_demo(fg)
    model = fg.model()
    e = parse_expression(NET, "orders", model)
    fg.put("net_expr", "MetricBinding",
           MetricBindingPayload(name="net_expr", label="Net", format="currency", expression=e))
    assert "net_expr" in fg.slice().dependents("orders.fraud_type")
    assert fg.model().metric("net_expr").expression.fields == e.fields


# The allowlist: only listed sqlglot node classes may appear in an expression metric.

@pytest.mark.parametrize("sql", [
    "max(read_csv('/etc/passwd'))",
    "max(read_parquet('x.parquet'))",
    "sum(length(read_text('/etc/passwd')))",
    "max(current_setting('x'))",
    "sum(length(orders.fraud_type))",
    "max(orders.fraud_type || 'x')",
    "max(cast(orders.loss_amount as blob))",
    "max(regexp_replace(orders.fraud_type, 'a', 'b'))",
    "sum(orders.loss_amount) over ()",
    "max(orders.order_created_date - interval 1 day)",
    "max(date_diff('nonsense', orders.order_created_date, orders.order_created_date))",
])
def test_expression_refuses_an_unlisted_function(model, sql):
    with pytest.raises(QueryRejected):
        parse_expression(sql, "orders", model)


def test_expression_refuses_read_csv(model):
    with pytest.raises(QueryRejected, match="ReadCSV|read_csv"):
        parse_expression("max(read_csv('/etc/passwd'))", "orders", model)


def test_expression_refuses_an_unknown_dialect(model):
    with pytest.raises(QueryRejected, match="dialect"):
        parse_expression("sum(orders.loss_amount)", "orders", model, dialect="mysql")
    e = parse_expression("sum(orders.loss_amount)", "orders", model)
    with pytest.raises(QueryRejected, match="dialect"):
        validate_expression(e.model_copy(update={"dialect": "mysql"}))


def test_expression_accepts_postgres(model):
    e = parse_expression("sum(orders.loss_amount)", "orders", model, dialect="postgres")
    assert e.dialect == "postgres"


@pytest.mark.parametrize("sql", [
    # aggregates
    "sum(orders.loss_amount)", "count(*)", "count(distinct orders.dealer_id)", "avg(orders.is_fraud)",
    "min(orders.order_value)", "max(orders.order_value)", "median(orders.order_value)",
    "quantile_cont(orders.order_value, 0.5)", "stddev(orders.order_value)",
    # arithmetic
    "sum(orders.loss_amount + orders.order_value - 1)", "sum(orders.loss_amount * 2 / 3)",
    "sum(-orders.loss_amount % 7)", "sum((orders.loss_amount))",
    "sum(abs(orders.loss_amount))", "sum(round(orders.loss_amount, 2))",
    # comparisons and boolean logic, in a filter
    "sum(orders.loss_amount) filter (where orders.fraud_type in ('a', 'b') and not orders.is_fraud"
    " between 1 and 2 or orders.fraud_type like '%x%' or orders.order_value >= 3"
    " or orders.order_value <> 4 or orders.fraud_type is null)",
    # conditionals
    "sum(case when orders.fraud_type = 'x' then orders.loss_amount else 0 end)",
    "sum(if(orders.is_fraud > 0, 1, 0))",
    # casts with a listed type
    "sum(cast(orders.is_fraud as double))", "sum(try_cast(orders.fraud_type as integer))",
    # null handling
    "sum(coalesce(orders.loss_amount, 0))", "max(nullif(orders.loss_amount, 0))",
    # dates
    "avg(date_diff('day', orders.order_created_date, orders.order_created_date))",
    "max(date_trunc('month', orders.order_created_date))",
    "max(extract(year from orders.order_created_date))",
    # literals
    "sum(case when true then null else 1.5 end)",
])
def test_expression_accepts_each_allowed_family(model, sql):
    e = parse_expression(sql, "orders", model)
    validate_expression(e, model)
