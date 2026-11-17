"""The retail demo: the generator, its planted stories, and the model on the built source."""
import duckdb
import pytest

from living_reports.demos import get_demo
from living_reports.demos.retail import ROWS

DEMO = get_demo("retail")


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    out = tmp_path_factory.mktemp("retail")
    DEMO.generate(out)
    return out


def q(folder, sql):
    con = duckdb.connect()
    for t in ("sales", "stores", "products"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{folder / (t + '.parquet')}')")
    return con.execute(sql).fetchall()


def one(folder, sql):
    return q(folder, sql)[0][0]


# --- shape ---------------------------------------------------------------------------------


def test_the_tables_have_their_rows_and_unique_keys(folder):
    assert one(folder, "SELECT count(*) FROM sales") == ROWS
    for table, key in DEMO.keys.items():
        rows, distinct = q(folder, f"SELECT count(*), count(DISTINCT {key[0]}) FROM {table}")[0]
        assert rows == distinct, table
    orphans = "SELECT count(*) FROM sales s LEFT JOIN {t} x USING ({k}) WHERE x.{k} IS NULL"
    assert one(folder, orphans.format(t="stores", k="store_id")) == 0
    assert one(folder, orphans.format(t="products", k="product_id")) == 0


def test_the_data_spans_2024_and_2025(folder):
    first, last = q(folder, "SELECT min(sale_date), max(sale_date) FROM sales")[0]
    assert (first.year, first.month, last.year, last.month) == (2024, 1, 2025, 12)


def test_the_generator_gives_the_same_data_every_time(folder, tmp_path):
    DEMO.generate(tmp_path)
    # An exact digest: a float sum can differ in its last bits with the order of a parallel scan.
    digest = "SELECT count(*), sum(hash(s)::HUGEINT) FROM sales s"
    assert q(folder, digest) == q(tmp_path, digest)


def test_moment_1_ends_before_december_and_lacks_the_promotion_code(folder):
    con = duckdb.connect()
    tables = DEMO.tables(folder, 1)
    last, = con.execute(f"SELECT max(sale_date) FROM ({tables['sales']})").fetchone()
    assert last.month == 11 and last.year == 2025
    columns = [c[0] for c in con.execute(f"DESCRIBE {tables['sales']}").fetchall()]
    assert "promotion_code" not in columns
    columns = [c[0] for c in con.execute(f"DESCRIBE {DEMO.tables(folder, 2)['sales']}").fetchall()]
    assert "promotion_code" in columns


# --- planted stories -----------------------------------------------------------------------

BY_YEAR = "SELECT sum(revenue) FILTER (year(sale_date) = 2025) / sum(revenue) FILTER (year(sale_date) = 2024) FROM sales WHERE channel = '{}'"
MARGIN = ("SELECT sum(gross_profit) / sum(revenue) FROM sales JOIN products USING (product_id) "
          "WHERE category = 'Electronics' AND {}")


def test_online_revenue_grows_about_60_percent_and_store_revenue_is_flat(folder):
    assert 1.45 <= one(folder, BY_YEAR.format("Online")) <= 1.75
    assert 0.9 <= one(folder, BY_YEAR.format("Store")) <= 1.1


def test_electronics_margin_falls_after_the_july_2025_discount_push(folder):
    assert 0.27 <= one(folder, MARGIN.format("sale_date < DATE '2025-07-01'")) <= 0.33
    assert 0.19 <= one(folder, MARGIN.format("sale_date >= DATE '2025-07-01'")) <= 0.25


def test_north_apparel_returns_spike_in_q4_2025(folder):
    sql = ("SELECT avg(is_returned) FROM sales JOIN stores USING (store_id) "
           "JOIN products USING (product_id) WHERE {}")
    spike = "region = 'North' AND category = 'Apparel' AND sale_date >= DATE '2025-10-01'"
    assert 0.15 <= one(folder, sql.format(spike)) <= 0.21
    others = q(folder, "SELECT max(r) FROM (SELECT avg(is_returned) r FROM sales JOIN stores USING "
                       "(store_id) JOIN products USING (product_id) WHERE NOT (" + spike + ") "
                       "GROUP BY region, category, quarter(sale_date), year(sale_date))")[0][0]
    assert others <= 0.12
    assert one(folder, "SELECT avg(is_returned) FROM sales") < 0.08  # the total meets the target


def test_november_and_december_are_about_1_6_times_an_average_month(folder):
    months = q(folder, "SELECT month(sale_date) m, sum(revenue) r FROM sales GROUP BY 1")
    mean = sum(r for _, r in months) / 12
    peak = sum(r for m, r in months if m in (11, 12)) / 2
    assert 1.4 <= peak / mean <= 1.8


def test_outlet_stores_miss_the_margin_target_in_every_quarter(folder):
    rows = q(folder, "SELECT store_format, year(sale_date) y, quarter(sale_date) qu, "
                     "sum(gross_profit) / sum(revenue) FROM sales JOIN stores USING (store_id) "
                     "GROUP BY ALL")
    assert all(m < 0.35 for f, _, _, m in rows if f == "Outlet")
    overall = dict(q(folder, "SELECT store_format, sum(gross_profit) / sum(revenue) FROM sales "
                             "JOIN stores USING (store_id) GROUP BY 1"))
    assert overall["Flagship"] >= 0.35 and overall["Standard"] >= 0.35


def test_a_new_channel_starts_in_december_2025(folder):
    first = one(folder, "SELECT min(sale_date) FROM sales WHERE channel = 'Social commerce'")
    assert (first.year, first.month) == (2025, 12)
