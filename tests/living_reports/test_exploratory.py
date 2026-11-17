import pytest

from living_reports.metrics.exploratory import SqlRejected, run_exploratory


@pytest.fixture
def model(store):
    return store.graph.model()


def test_select_runs(model):
    r = run_exploratory(
        "select sales_channel, count(*) n from orders group by 1 order by 1", "duckdb_demo", model)
    assert r.columns == ["sales_channel", "n"] and r.row_count == 3


@pytest.mark.parametrize("sql", [
    "delete from orders",
    "drop table orders",
    "update orders set sales_channel='x'",
    "select 1; select 2",
    "copy orders to '/tmp/x.csv'",
    "attach '/tmp/x.db'",
    "pragma database_list",
    "",
])
def test_anything_but_one_select_is_rejected(model, sql):
    with pytest.raises(SqlRejected):
        run_exploratory(sql, "duckdb_demo", model)


def test_two_statements_are_rejected(model):
    with pytest.raises(SqlRejected, match="Only one SELECT"):
        run_exploratory("select 1 as x; select order_id from orders", "duckdb_demo", model)


def test_file_access_is_blocked(model):
    with pytest.raises(SqlRejected):
        run_exploratory("select * from read_csv('/etc/passwd')", "duckdb_demo", model)


def test_table_outside_the_model_is_rejected(model):
    with pytest.raises(SqlRejected, match="secrets"):
        run_exploratory("select * from main.secrets", "duckdb_demo", model)


def test_column_outside_the_model_is_rejected(model):
    with pytest.raises(SqlRejected, match="phone_line_type"):
        run_exploratory("select phone_line_type from orders", "duckdb_demo", model)


def test_rows_are_capped(model):
    assert run_exploratory("select order_id from orders", "duckdb_demo", model).row_count == 1000


def test_hash_differs_by_sql(model):
    a = run_exploratory("select 1 as x", "duckdb_demo", model)
    b = run_exploratory("select 2 as x", "duckdb_demo", model)
    assert a.query_hash != b.query_hash


def test_exploratory_runs_the_regenerated_sql(model):
    r = run_exploratory("select sales_channel   from orders where sales_channel = 'App' "
                        "-- a comment", "duckdb_demo", model)
    assert r.sql == ('SELECT "orders"."sales_channel" AS "sales_channel" FROM "main"."orders" '
                     'AS "orders" WHERE "orders"."sales_channel" = \'App\'')
    assert {row[0] for row in r.rows} == {"App"}


@pytest.mark.parametrize("sql", ["select * from orders", "select o.* from orders o",
                                 "select columns(*) from orders"])
def test_exploratory_refuses_star(model, sql):
    with pytest.raises(SqlRejected, match="name each column"):
        run_exploratory(sql, "duckdb_demo", model)


def test_exploratory_allows_count_star(model):
    assert run_exploratory("select count(*) as n from orders", "duckdb_demo", model).rows == [[2800]]


def test_exploratory_refuses_read_csv(model):
    with pytest.raises(SqlRejected):
        run_exploratory("select x.a from read_csv('/etc/passwd') x", "duckdb_demo", model)


def test_exploratory_refuses_two_statements(model):
    with pytest.raises(SqlRejected):
        run_exploratory("select order_id from orders; select order_id from orders", "duckdb_demo", model)


def test_exploratory_refuses_a_row_as_a_column(model):
    with pytest.raises(SqlRejected, match="orders"):
        run_exploratory("select orders from orders", "duckdb_demo", model)


def test_duckdb_must_also_see_one_select(model, monkeypatch):
    import sqlglot

    from living_reports.metrics import exploratory

    monkeypatch.setattr(exploratory, "check_select",
                        lambda ast, m, d: sqlglot.parse_one("DELETE FROM orders", read="duckdb"))
    with pytest.raises(SqlRejected, match="Only one SELECT"):
        run_exploratory("select order_id from orders", "duckdb_demo", model)


def test_timeout_is_a_rejection(model, monkeypatch):
    from living_reports.metrics import exploratory
    monkeypatch.setattr(exploratory, "TIMEOUT_S", 0.2)
    slow = "select count(*) as n from orders a, orders b, orders c, orders d"
    with pytest.raises(SqlRejected, match="did not answer"):
        run_exploratory(slow, "duckdb_demo", model)
    assert run_exploratory("select count(*) as n from orders", "duckdb_demo", model).rows == [[2800]]


def test_result_carries_source_and_data_time(model, demo_source):
    r = run_exploratory("select count(*) as n from orders", "duckdb_demo", model)
    assert r.source_id == "duckdb_demo" and r.data_time == demo_source.data_time()


def test_a_rebuild_before_the_run_gives_the_data_time_of_the_rows(model, monkeypatch, demo_source):
    from living_reports.sources import DuckDBSource
    from living_reports.sources.build import build_demo_source
    real = DuckDBSource.run

    def rebuild_first(self, *args, **kwargs):
        build_demo_source(2)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(DuckDBSource, "run", rebuild_first)
    r = run_exploratory("select count(*) as n from orders", "duckdb_demo", model)
    assert r.rows == [[3000]] and r.data_time == demo_source.data_time()
