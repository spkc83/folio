"""The transaction fraud demo: the generator and its planted stories."""
import duckdb
import pytest

from living_reports.demos import get_demo
from living_reports.demos.fraud import ROWS

DEMO = get_demo("fraud")
J = "transactions JOIN merchants USING (merchant_id) JOIN cards USING (card_id)"


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    out = tmp_path_factory.mktemp("fraud")
    DEMO.generate(out)
    return out


def q(folder, sql):
    con = duckdb.connect()
    for t in ("transactions", "merchants", "cards"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{folder / (t + '.parquet')}')")
    return con.execute(sql).fetchall()


def one(folder, sql):
    return q(folder, sql)[0][0]


def test_the_tables_have_their_rows_and_unique_keys(folder):
    assert one(folder, "SELECT count(*) FROM transactions") == ROWS
    for table, key in DEMO.keys.items():
        rows, distinct = q(folder, f"SELECT count(*), count(DISTINCT {key[0]}) FROM {table}")[0]
        assert rows == distinct, table
    assert one(folder, "SELECT count(*) FROM transactions LEFT JOIN merchants m USING (merchant_id) WHERE m.merchant_id IS NULL") == 0
    assert one(folder, "SELECT count(*) FROM transactions LEFT JOIN cards c USING (card_id) WHERE c.card_id IS NULL") == 0


def test_the_generator_gives_the_same_data_every_time(folder, tmp_path):
    DEMO.generate(tmp_path)
    # An exact digest: a float sum can differ in its last bits with the order of a parallel scan.
    digest = "SELECT count(*), sum(hash(t)::HUGEINT) FROM transactions t"
    assert q(folder, digest) == q(tmp_path, digest)


def test_moment_1_lacks_december_and_the_3ds_result(folder):
    con = duckdb.connect()
    sql = DEMO.tables(folder, 1)["transactions"]
    assert con.execute(f"SELECT max(txn_ts) FROM ({sql})").fetchone()[0].month == 11
    assert "three_ds_result" not in [c[0] for c in con.execute(f"DESCRIBE {sql}").fetchall()]


def test_the_overall_fraud_rate_meets_the_target(folder):
    assert 0.003 <= one(folder, "SELECT avg(is_fraud) FROM transactions") < 0.005


def test_a_card_testing_burst_in_august_2025_hits_digital_goods(folder):
    rate = ("SELECT avg(is_fraud) FROM " + J + " WHERE merchant_category = 'Digital goods' "
            "AND date_trunc('month', txn_ts) = DATE '{}'")
    assert 0.045 <= one(folder, rate.format("2025-08-01")) <= 0.08
    assert one(folder, rate.format("2025-07-01")) < 0.012
    small = one(folder, "SELECT median(amount) FROM transactions WHERE fraud_type = 'Card testing' "
                        "AND date_trunc('month', txn_ts) = DATE '2025-08-01'")
    assert small < 10


def test_cross_border_fraud_is_about_4_times_domestic(folder):
    rates = dict(q(folder, "SELECT border, avg(is_fraud) FROM transactions GROUP BY 1"))
    assert 3.0 <= rates["Cross-border"] / rates["Domestic"] <= 5.5


def test_contactless_fraud_rises_in_every_quarter_of_2025(folder):
    rows = q(folder, "SELECT quarter(txn_ts), avg(is_fraud) FROM transactions WHERE channel = "
                     "'Contactless' AND year(txn_ts) = 2025 GROUP BY 1 ORDER BY 1")
    rates = [r for _, r in rows]
    assert len(rates) == 4 and all(a < b for a, b in zip(rates, rates[1:]))


def test_prepaid_cards_miss_the_target_and_platinum_loses_most_per_fraud(folder):
    rates = dict(q(folder, "SELECT card_type, avg(is_fraud) FROM " + J + " GROUP BY 1"))
    assert rates["Prepaid"] > 0.005 and rates["Debit"] < 0.005 and rates["Credit"] < 0.005
    per = dict(q(folder, "SELECT card_tier, sum(fraud_loss) / sum(is_fraud) FROM " + J + " GROUP BY 1"))
    assert per["Platinum"] == max(per.values())


def test_south_electronics_has_the_largest_fraud_loss(folder):
    top = q(folder, "SELECT merchant_region, merchant_category FROM " + J + " GROUP BY ALL "
                    "ORDER BY sum(fraud_loss) DESC LIMIT 1")[0]
    assert top == ("South", "Electronics")


def test_a_new_channel_starts_in_december_2025(folder):
    first = one(folder, "SELECT min(txn_ts) FROM transactions WHERE channel = 'Mobile wallet'")
    assert (first.year, first.month) == (2025, 12)
