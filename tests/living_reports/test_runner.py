import pytest

from living_reports.store import get_store
from living_reports.semantic.demo import seed_demo
from living_reports.engine import Filter, Query, TimeRange
from living_reports.metrics.runner import format_value, run_query, to_facts


@pytest.fixture
def seeded(store):
    return store


@pytest.fixture
def seeded2(store2):
    return store2


@pytest.fixture
def seeded_real(real_store):
    return real_store


def test_zero_rows_gives_empty_fact(seeded):
    r = run_query(Query(metrics=["fraud_rate"], filters=[Filter(member="orders.sales_channel", op="eq", values=["Marketplace"])]), "duckdb_demo", seeded)
    fact = to_facts(r, "x")[0]
    assert fact.state == "empty" and fact.formatted == "No data" and fact.value is None


def test_breakdown_with_no_rows_gives_no_rows(seeded):
    r = run_query(Query(metrics=["fraud_rate"], dimensions=["dealers.region"], filters=[Filter(member="orders.sales_channel", op="eq", values=["Marketplace"])]), "duckdb_demo", seeded)
    assert r.row_count == 0 and to_facts(r, "x") == []


def test_fact_carries_its_trace(seeded2):
    r = run_query(Query(metrics=["order_count"]), "duckdb_demo", seeded2)
    fact = to_facts(r, "t")[0]
    assert fact.query_hash == r.query_hash and fact.metric == "order_count"
    assert fact.metric_version == 1 and fact.source_id == "duckdb_demo" and fact.formatted == "3,000"


def test_breakdown_facts_have_stable_ids(seeded2):
    facts = to_facts(run_query(Query(metrics=["order_count"], dimensions=["orders.sales_channel"]), "duckdb_demo", seeded2), "c")
    assert "c.order_count.Marketplace" in {f.id for f in facts}


def test_result_keeps_sql_for_the_checker(seeded2):
    r = run_query(Query(metrics=["order_count"]), "duckdb_demo", seeded2)
    assert r.sql.startswith("SELECT") and r.params == []


def test_result_is_cached_by_hash(seeded2):
    q = Query(metrics=["order_count"])
    assert run_query(q, "duckdb_demo", seeded2).from_cache is False
    assert run_query(q, "duckdb_demo", seeded2).from_cache is True


def test_cache_misses_after_the_source_changes(seeded):
    from living_reports.sources.build import build_demo_source
    q = Query(metrics=["order_count"])
    before = run_query(q, "duckdb_demo", seeded)
    assert run_query(q, "duckdb_demo", seeded).from_cache is True
    build_demo_source(2)
    after = run_query(q, "duckdb_demo", seeded)
    assert after.from_cache is False
    assert before.rows == [[2800]] and after.rows == [[3000]]


def test_a_rebuilt_source_with_the_same_moment_is_not_served_from_cache(seeded, orders_file):
    """The old snapshot cache keyed rows by name; a rebuilt file served old rows."""
    import os
    from living_reports.sources.build import build_demo_source
    q = Query(metrics=["order_count"])
    assert run_query(q, "duckdb_demo", seeded).rows == [[2800]]
    import duckdb
    smaller = orders_file.with_name("smaller.parquet")
    duckdb.sql(f"COPY (SELECT * FROM '{orders_file}' LIMIT 100) TO '{smaller}' (FORMAT PARQUET)")
    os.replace(smaller, orders_file)
    os.utime(orders_file, (2_000_000_000, 2_000_000_000))
    build_demo_source(1)
    again = run_query(q, "duckdb_demo", seeded)
    assert again.from_cache is False and again.rows == [[100]]


def test_result_carries_source_and_data_time(seeded, demo_source):
    r = run_query(Query(metrics=["order_count"]), "duckdb_demo", seeded)
    assert r.source_id == "duckdb_demo"
    assert r.data_time == demo_source.data_time()
    cached = run_query(Query(metrics=["order_count"]), "duckdb_demo", seeded)
    assert cached.from_cache and cached.data_time == r.data_time


def test_facts_carry_data_time(seeded, demo_source):
    fact = to_facts(run_query(Query(metrics=["order_count"]), "duckdb_demo", seeded), "t")[0]
    assert fact.source_id == "duckdb_demo" and fact.data_time == demo_source.data_time()


def test_a_query_cache_keyed_by_snapshot_is_dropped(tmp_path):
    import sqlite3
    from living_reports.store.sqlite import Store
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE query_cache (query_hash TEXT, snapshot TEXT, payload TEXT, "
                     "PRIMARY KEY (query_hash, snapshot))")
    s = Store(path)
    columns = [r[1] for r in s.rows("PRAGMA table_info(query_cache)")]
    assert columns == ["query_hash", "source_id", "data_time", "payload"]
    s.close()


def test_a_fact_saved_with_a_snapshot_reads_as_the_demo_source():
    from living_reports.metrics.runner import Fact
    f = Fact.model_validate({"id": "a", "label": "L", "value": 1, "formatted": "1",
                             "query_hash": "h", "snapshot": "s2"})
    assert f.source_id == "duckdb_demo" and f.data_time is None


@pytest.mark.parametrize("value,fmt,text", [
    (52_590_000.0, "currency", "$52.6M"),
    (9_500.0, "currency", "$9.5K"),
    (12.5, "currency", "$12.50"),
    (0.0352, "percent", "3.52%"),
    (1200000, "count", "1,200,000"),
    (float("nan"), "percent", "No data"),
    (None, "currency", "No data"),
])
def test_formats(value, fmt, text):
    assert format_value(value, fmt) == text


@pytest.mark.demo_data
def test_real_fraud_loss(seeded_real):
    loss = to_facts(run_query(Query(metrics=["fraud_loss"]), "duckdb_demo", seeded_real), "t")[0]
    assert loss.formatted == "$52.6M"


def test_share_facts_sum_to_one(seeded2):
    from living_reports.metrics.runner import share_facts
    r = run_query(Query(metrics=["order_count"], dimensions=["orders.sales_channel"]), "duckdb_demo", seeded2)
    facts = share_facts(r, "order_count", "sh")
    assert abs(sum(f.value for f in facts) - 1.0) < 1e-9
    assert all(f.formatted.endswith("%") and f.query_hash == r.query_hash for f in facts)


def test_share_of_zero_total_is_empty(seeded2):
    from living_reports.metrics.runner import share_facts
    r = run_query(Query(metrics=["fraud_loss"], dimensions=["dealers.region"], filters=[Filter(member="orders.fraud_type", op="eq", values=["none"])]), "duckdb_demo", seeded2)
    assert {f.state for f in share_facts(r, "fraud_loss", "sh")} == {"empty"}


def test_facts_carry_the_metric_definition(seeded):
    r = run_query(Query(metrics=["fraud_loss"]), "duckdb_demo", seeded)
    assert to_facts(r, "x")[0].definition == "Total loss on orders confirmed as fraud."


def test_ref_of():
    from living_reports.metrics.runner import ref_of
    assert ref_of("q1_total.fraud_loss") == "q1"
    assert ref_of("q1.fraud_loss.share.telesales") == "q1"
    assert ref_of("q12.order_count") == "q12"


def test_facts_carry_a_binding(seeded):
    from living_reports.metrics.runner import Binding, share_facts
    r = run_query(Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"]), "duckdb_demo", seeded)
    by_id = {f.id: f for f in to_facts(r, "q1")}
    app = Binding(ref="q1", metric="fraud_loss", keys={"orders.sales_channel": "App"})
    assert by_id["q1.fraud_loss.App"].binding == app
    total = to_facts(run_query(Query(metrics=["fraud_loss"]), "duckdb_demo", seeded), "q1_total")[0]
    assert total.binding == Binding(ref="q1", metric="fraud_loss", keys={})
    share = share_facts(r, "fraud_loss", "q1")[0]
    assert share.binding.share is True and share.binding.keys == {"orders.sales_channel": share.id.split(".")[-1]}


def test_a_monthly_fact_binds_to_the_cube_month(seeded):
    from living_reports.metrics.runner import Binding
    r = run_query(Query(metrics=["fraud_loss"], time=TimeRange(grain="month")), "duckdb_demo", seeded)
    first = to_facts(r, "q1")[0]
    assert first.binding == Binding(ref="q1", metric="fraud_loss", keys={"month": "2024-01-01"})


def test_result_dimensions_are_member_ids(seeded2):
    r = run_query(Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"]), "duckdb_demo", seeded2)
    assert r.dimensions == ["orders.sales_channel"]
    assert r.columns == ["orders.sales_channel", "fraud_loss"]
    assert r.labels == {"orders.sales_channel": "Sales channel", "fraud_loss": "Fraud loss"}


def test_time_series_dimension_is_the_time_column(seeded2):
    r = run_query(Query(metrics=["fraud_loss"], time=TimeRange(grain="month")), "duckdb_demo", seeded2)
    assert r.dimensions == ["orders.order_created_date:month"]
    assert r.rows[0][0] == "2024-01-01"
    assert r.labels["orders.order_created_date:month"] == "Month"


def test_overrides_preview_without_changing_the_graph(seeded2):
    from living_reports.semantic.kinds import MetricBindingPayload

    from .conftest import friendly_payload

    q = Query(metrics=["fraud_loss"])
    before = run_query(q, "duckdb_demo", seeded2)
    payload = MetricBindingPayload.model_validate(friendly_payload(seeded2))
    after = run_query(q, "duckdb_demo", seeded2, overrides={"fraud_loss": payload})
    assert after.rows[0][0] < before.rows[0][0]
    assert after.metric_versions["fraud_loss"] == 2
    assert run_query(q, "duckdb_demo", seeded2).rows == before.rows


def test_result_carries_the_model_version(seeded2):
    r = run_query(Query(metrics=["order_count"]), "duckdb_demo", seeded2)
    assert r.graph_version == seeded2.graph.version()


def test_a_rebuild_between_stamp_and_run_gives_the_data_time_of_the_rows(seeded, monkeypatch):
    """The result's data time is the one the source read, not the one stamped before."""
    from living_reports.sources import DuckDBSource
    from living_reports.sources.build import build_demo_source
    from living_reports.sources.registry import source_for
    real = DuckDBSource.run

    def rebuild_first(self, *args, **kwargs):
        build_demo_source(2)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(DuckDBSource, "run", rebuild_first)
    r = run_query(Query(metrics=["order_count"]), "duckdb_demo", seeded)
    assert r.rows == [[3000]]
    assert r.data_time == source_for("duckdb_demo").data_time()
    assert to_facts(r, "t")[0].data_time == r.data_time
    monkeypatch.setattr(DuckDBSource, "run", real)
    again = run_query(Query(metrics=["order_count"]), "duckdb_demo", seeded)
    assert again.from_cache and again.rows == [[3000]]
