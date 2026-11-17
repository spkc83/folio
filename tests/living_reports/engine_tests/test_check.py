import pytest

from living_reports.engine import Query, QueryRejected
from living_reports.engine.check import check_select, parse_select
from living_reports.engine.compile import compile

from .test_compile import spec_query


def _check(sql, model):
    check_select(parse_select(sql, "duckdb"), model, "duckdb")


def test_compiled_queries_pass(model):
    c = compile(spec_query(), model)
    check_select(c.ast, model, "duckdb")
    c = compile(Query(metrics=["fraud_loss", "dealer_count"], dimensions=["dealers.region"]), model)
    check_select(c.ast, model, "duckdb")


def test_unknown_column_is_rejected(model):
    with pytest.raises(QueryRejected, match="nope"):
        _check("select nope from main.orders", model)


def test_unknown_table_is_rejected(model):
    with pytest.raises(QueryRejected, match="secrets"):
        _check("select s.id from main.secrets s", model)
    with pytest.raises(QueryRejected, match="secrets"):
        _check("select o.order_id from main.orders o join secrets s on o.order_id = s.id", model)


def test_table_functions_are_rejected(model):
    with pytest.raises(QueryRejected):
        _check("select * from read_csv('/etc/passwd')", model)


def test_two_statements_are_rejected():
    with pytest.raises(QueryRejected, match="Only one SELECT statement is allowed."):
        parse_select("select 1; select 2", "duckdb")


@pytest.mark.parametrize("sql", ["pragma version", "delete from orders", "drop table orders",
                                 "attach 'x.db'", "", "copy orders to 'x.csv'"])
def test_non_selects_are_rejected(sql):
    with pytest.raises(QueryRejected):
        parse_select(sql, "duckdb")


def test_parse_error_is_rejected():
    with pytest.raises(QueryRejected):
        parse_select("select from where", "duckdb")


def test_cte_and_union_are_allowed(model):
    tree = parse_select("with a as (select 1 as x) select a.x from a", "duckdb")
    check_select(tree, model, "duckdb")
    union = parse_select(
        "select sales_channel from main.orders union all select fraud_type from main.orders",
        "duckdb")
    check_select(union, model, "duckdb")


def test_unqualified_column_on_the_one_table_is_allowed(model):
    _check("select sales_channel from main.orders", model)
    _check("select sales_channel, count(*) n from orders group by 1 order by 1", model)


def test_cte_cannot_hide_an_unknown_table(model):
    with pytest.raises(QueryRejected, match="secrets"):
        _check("with a as (select s.id from main.secrets s) select a.id from a", model)


def test_check_refuses_a_cte_name_reused_for_an_undeclared_table(model):
    # The inner scope defines a CTE named secrets; the outer scope reads the real table.
    sql = ("select t.x from secrets, (with secrets as (select 1 as x) select x from secrets) t")
    with pytest.raises(QueryRejected, match="secrets"):
        _check(sql, model)


def test_a_cte_visible_in_a_nested_scope_is_allowed(model):
    _check("with a as (select sales_channel from main.orders) "
           "select t.sales_channel from (select sales_channel from a) t", model)


@pytest.mark.parametrize("sql", [
    "select * from read_csv('/etc/passwd')",
    "select x.a from read_parquet('x.parquet') x",
    "select (select count(*) from read_csv('/etc/passwd')) as n from main.orders",
    "select * from glob('/etc/*')",
    "select read_text('/etc/passwd') as t from main.orders",
    "select getenv('HOME') as h from main.orders",
])
def test_check_refuses_table_and_file_functions_anywhere(model, sql):
    with pytest.raises(QueryRejected):
        _check(sql, model)


@pytest.mark.parametrize("sql", [
    "select * from main.orders",
    "select o.* from main.orders o",
    "select * exclude (order_id) from main.orders",
    "select columns(*) from main.orders",
])
def test_check_refuses_a_star_projection(model, sql):
    with pytest.raises(QueryRejected, match=r"\*|column"):
        _check(sql, model)


def test_count_star_is_allowed(model):
    _check("select count(*) as n from main.orders", model)


def test_check_refuses_a_row_as_a_column(model):
    # DuckDB reads a table alias in the select list as the whole row (a struct).
    with pytest.raises(QueryRejected, match="orders"):
        _check("select orders from main.orders", model)


def test_check_returns_the_qualified_ast(model):
    tree = check_select(parse_select("select sales_channel from orders", "duckdb"), model, "duckdb")
    assert tree.sql(dialect="duckdb") == (
        'SELECT "orders"."sales_channel" AS "sales_channel" FROM "main"."orders" AS "orders"')


def test_order_by_an_output_alias_is_allowed(model):
    _check("select sales_channel, count(*) as n from main.orders group by 1 order by n", model)


# Review fixes (C1, I2)

@pytest.mark.parametrize("sql", [
    "select p.x from (pivot main.secrets on k using count(*)) p",
    "select p.App from (pivot main.orders on sales_channel using sum(loss_amount)) p",
    "pivot main.secrets on k using count(*)",
])
def test_pivot_cannot_hide_a_table(model, sql):
    with pytest.raises(QueryRejected):
        _check(sql, model)


def test_a_scope_that_sqlglot_cannot_walk_is_refused_not_a_crash(model, monkeypatch):
    from living_reports.engine import check

    def boom(ast):
        raise AssertionError("cannot walk")

    monkeypatch.setattr(check, "traverse_scope", boom)
    with pytest.raises(QueryRejected):
        _check("select sales_channel from main.orders", model)


@pytest.mark.parametrize("sql", [
    "select #9 from main.orders",
    "select #1 from main.orders",
    "select sales_channel from main.orders order by #1",
])
def test_positional_columns_are_refused(model, sql):
    with pytest.raises(QueryRejected, match="name each column"):
        _check(sql, model)


@pytest.mark.parametrize("sql", [
    "select s.i from main.orders s(a, b, c, d, e, f, g, h, i)",
    "select s.a from main.orders as s(a)",
])
def test_column_aliases_on_a_dataset_table_are_refused(model, sql):
    with pytest.raises(QueryRejected, match="column list"):
        _check(sql, model)


def test_column_aliases_on_derived_tables_are_allowed(model):
    _check("select t.a from (values (1, 2)) t(a, b)", model)
    _check("select t.c from (select sales_channel from main.orders) t(c)", model)
