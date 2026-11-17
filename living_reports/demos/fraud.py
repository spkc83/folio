"""The transaction fraud demo: about 400,000 card transactions, January 2024 to December 2025.

Planted stories (the tests in ``test_demo_fraud.py`` hold each one to a range):

1. A card-testing burst in August 2025: many small Online transactions at Digital goods
   merchants; the Digital goods fraud rate jumps to about 6% that month.
2. The cross-border fraud rate is about 4 times the domestic rate.
3. Contactless fraud rises in every quarter of 2025.
4. Prepaid cards miss the 0.5% fraud-rate target; Platinum cards lose the most per fraud.
5. Electronics in the South has the largest fraud loss of any category and region.
6. A new channel, Mobile wallet, starts in December 2025 (it arrives with the new data).
"""
from pathlib import Path

import duckdb

from living_reports.semantic.kinds import FilterSpec, Measure, MetricBindingPayload, Ratio

from . import Definition, Demo, moment_tables, u, write_parquet
from ._model import D, F, R, seed_model

ROWS = 400_000
BURST = 400  # card-testing transactions in August 2025
MERCHANTS = 800
CARDS = 60_000


def generate(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"""
    CREATE TABLE merchants AS
    SELECT 'M' || lpad((i + 1)::VARCHAR, 4, '0') AS merchant_id,
           CASE WHEN c < 0.25 THEN 'Grocery' WHEN c < 0.45 THEN 'Restaurants'
                WHEN c < 0.60 THEN 'Fuel' WHEN c < 0.72 THEN 'Electronics'
                WHEN c < 0.82 THEN 'Travel' ELSE 'Digital goods' END AS merchant_category,
           ['North', 'South', 'East', 'West'][1 + floor({u('i', 'region')} * 4)::INT] AS merchant_region
    FROM (SELECT i, {u('i', 'category')} AS c FROM range({MERCHANTS}) t(i))""")
    con.execute(f"""
    CREATE TABLE cards AS
    SELECT 'C' || lpad((i + 1)::VARCHAR, 6, '0') AS card_id, card_type,
           CASE WHEN card_type = 'Prepaid' OR t < 0.70 THEN 'Standard'
                WHEN t < 0.90 THEN 'Gold' ELSE 'Platinum' END AS card_tier
    FROM (SELECT i, {u('i', 'tier')} AS t,
                 CASE WHEN {u('i', 'type')} < 0.55 THEN 'Debit'
                      WHEN {u('i', 'type')} < 0.90 THEN 'Credit' ELSE 'Prepaid' END AS card_type
          FROM range({CARDS}) t(i))""")
    con.execute("""
    CREATE TABLE days AS
    SELECT d::DATE AS day, sum(w) OVER (ORDER BY d) / sum(w) OVER () AS c
    FROM (SELECT d, (CASE month(d) WHEN 12 THEN 1.3 ELSE 1.0 END)
                    * (CASE WHEN year(d) = 2025 THEN 1.1 ELSE 1.0 END) AS w
          FROM range(TIMESTAMP '2024-01-01', TIMESTAMP '2026-01-01', INTERVAL 1 DAY) t(d))""")
    base = ROWS - BURST
    con.execute(f"""
    CREATE TABLE picks AS
    SELECT b.i, d.day, false AS burst,
           'C' || lpad((1 + floor({u('b.i', 'card')} * {CARDS})::INT)::VARCHAR, 6, '0') AS card_id,
           'M' || lpad((1 + floor({u('b.i', 'merchant')} * {MERCHANTS})::INT)::VARCHAR, 4, '0') AS merchant_id
    FROM (SELECT i, {u('i', 'day')} AS u_day FROM range({base}) t(i)) b
    ASOF JOIN days d ON d.c >= b.u_day
    UNION ALL
    SELECT i, DATE '2025-08-01' + floor({u('i', 'burst_day')} * 31)::INT, true,
           'C' || lpad((1 + floor({u('i', 'card')} * {CARDS})::INT)::VARCHAR, 6, '0'),
           (SELECT min(merchant_id) FROM merchants WHERE merchant_category = 'Digital goods')
    FROM range({base}, {ROWS}) t(i)""")
    con.execute(f"""
    CREATE TABLE transactions AS
    WITH x AS (
      SELECT p.*, m.merchant_category AS cat, m.merchant_region AS region, c.card_type,
             c.card_tier,
             {u('p.i', 'channel')} AS u_channel, {u('p.i', 'border')} AS u_border,
             {u('p.i', 'amount')} AS u_amount, {u('p.i', 'fraud')} AS u_fraud,
             {u('p.i', 'kind')} AS u_kind, {u('p.i', 'loss')} AS u_loss,
             {u('p.i', 'decline')} AS u_decline, {u('p.i', 'chargeback')} AS u_cb,
             {u('p.i', '3ds')} AS u_3ds, {u('p.i', 'time')} AS u_time
      FROM picks p JOIN merchants m USING (merchant_id) JOIN cards c USING (card_id)
    ), shaped AS (
      SELECT *,
        CASE WHEN NOT burst AND cat <> 'Digital goods' AND day >= DATE '2025-12-01'
                  AND u_channel >= 0.95 THEN 'Mobile wallet'
             WHEN burst OR cat = 'Digital goods' THEN 'Online'
             WHEN cat = 'Travel' AND u_channel < 0.7 THEN 'Online'
             WHEN cat = 'Fuel' THEN CASE WHEN u_channel < 0.5 THEN 'Contactless' ELSE 'Card present' END
             WHEN u_channel < 0.20 THEN 'Online'
             WHEN u_channel < CASE WHEN year(day) = 2025 THEN 0.65 ELSE 0.55 END THEN 'Contactless'
             ELSE 'Card present' END AS channel
      FROM x
    ), risk AS (
      SELECT *,
        CASE WHEN u_border < CASE WHEN cat = 'Travel' THEN 0.30 WHEN channel = 'Online' THEN 0.10
                                  ELSE 0.03 END THEN 'Cross-border' ELSE 'Domestic' END AS border,
        CASE WHEN burst THEN 1 + floor(u_amount * 4)
             ELSE round(CASE cat WHEN 'Grocery' THEN 55 WHEN 'Restaurants' THEN 40 WHEN 'Fuel' THEN 60
                                 WHEN 'Electronics' THEN 380 WHEN 'Travel' THEN 450 ELSE 25 END
                        * (0.3 + 1.4 * u_amount)
                        * CASE card_tier WHEN 'Platinum' THEN 1.6 WHEN 'Gold' THEN 1.2 ELSE 1.0 END, 2)
        END AS amount
      FROM shaped
    ), scored AS (
      SELECT *,
        CASE WHEN burst THEN 0.5
             ELSE 0.0018
                  * CASE WHEN border = 'Cross-border' THEN 5.0 ELSE 1.0 END
                  * CASE card_type WHEN 'Prepaid' THEN 3.0 ELSE 1.0 END
                  * CASE WHEN cat = 'Electronics' AND region = 'South' THEN 3.0 ELSE 1.0 END
                  * CASE WHEN channel = 'Contactless' AND year(day) = 2025
                         THEN [1.5, 2.2, 3.0, 4.0][quarter(day)] ELSE 1.0 END
        END AS fraud_p
      FROM risk
    ), labelled AS (
      SELECT *, (u_fraud < fraud_p) AS fraud FROM scored
    )
    SELECT
      'T' || lpad((row_number() OVER (ORDER BY day, i))::VARCHAR, 7, '0') AS txn_id,
      day + INTERVAL (floor(u_time * 86399)::INT) SECOND AS txn_ts,
      card_id, merchant_id, channel, border, amount,
      (u_decline < 0.02 + CASE WHEN fraud THEN 0.25 ELSE 0.0 END)::INT AS is_declined,
      fraud::INT AS is_fraud,
      CASE WHEN NOT fraud THEN 'No fraud'
           WHEN burst THEN 'Card testing'
           WHEN channel = 'Online' THEN CASE WHEN u_kind < 0.6 THEN 'Account takeover' ELSE 'Card testing' END
           WHEN channel = 'Card present' THEN CASE WHEN u_kind < 0.6 THEN 'Counterfeit' ELSE 'Lost or stolen' END
           ELSE 'Lost or stolen' END AS fraud_type,
      CASE WHEN fraud AND u_decline >= 0.02 + 0.25 THEN round(amount * (0.6 + 0.4 * u_loss), 2)
           ELSE 0.0 END AS fraud_loss,
      (CASE WHEN fraud THEN u_cb < 0.85 ELSE u_cb < 0.002 END)::INT AS is_chargeback,
      CASE WHEN channel <> 'Online' THEN 'Not used'
           WHEN u_3ds < 0.80 THEN 'Passed' WHEN u_3ds < 0.85 THEN 'Failed'
           ELSE 'Not used' END AS three_ds_result
    FROM labelled
    ORDER BY txn_ts""")
    write_parquet(con, folder, {"transactions": "*", "merchants": "*", "cards": "*"})


def _tables(folder: Path, moment: int) -> dict[str, str]:
    return moment_tables(folder, moment, "transactions", "txn_ts", "three_ds_result",
                         ["merchants", "cards"])


DATASETS = {
    "transactions": D("txn_id", {
        "txn_id": F("string"),
        "txn_ts": F("datetime", "Transaction date", time=True),
        "card_id": F("string"),
        "merchant_id": F("string"),
        "channel": F("string", "Channel"),
        "border": F("string", "Border", synonyms=["cross-border", "domestic"]),
        "amount": F("decimal"),
        "is_declined": F("integer"),
        "is_fraud": F("integer"),
        "fraud_type": F("string", "Fraud type"),
        "fraud_loss": F("decimal"),
        "is_chargeback": F("integer"),
    }, time_field="txn_ts"),
    "merchants": D("merchant_id", {
        "merchant_id": F("string"),
        "merchant_category": F("string", "Merchant category", synonyms=["category", "merchant type"]),
        "merchant_region": F("string", "Region", synonyms=["merchant region"]),
    }),
    "cards": D("card_id", {
        "card_id": F("string"),
        "card_type": F("string", "Card type"),
        "card_tier": F("string", "Card tier"),
    }),
}


def _m(name, label, fmt, definition, better=None, target=None, *, measure=None, ratio=None,
       synonyms=()):
    return MetricBindingPayload(name=name, label=label, format=fmt, definition=definition,
                                measure=measure, ratio=ratio, better=better, target=target,
                                synonyms=list(synonyms))


METRICS = [
    _m("transaction_count", "Transactions", "count", "Number of card transactions.",
       measure=Measure(agg="count", dataset="transactions")),
    _m("transaction_value", "Transaction value", "currency", "Total value of card transactions.",
       "higher", measure=Measure(agg="sum", field="transactions.amount"),
       synonyms=["spend", "volume"]),
    _m("fraud_rate", "Fraud rate", "percent", "Share of transactions confirmed as fraud.",
       "lower", 0.005, measure=Measure(agg="avg", field="transactions.is_fraud")),
    _m("fraud_cases", "Fraud cases", "count", "Number of transactions confirmed as fraud.",
       "lower", measure=Measure(agg="sum", field="transactions.is_fraud")),
    _m("fraud_loss", "Fraud loss", "currency", "Money lost on fraud transactions that were approved.",
       "lower", measure=Measure(agg="sum", field="transactions.fraud_loss")),
    _m("fraud_loss_rate", "Fraud loss rate", "percent", "Fraud loss as a share of transaction value.",
       "lower", 0.003, ratio=Ratio(numerator="fraud_loss", denominator="transaction_value"),
       synonyms=["loss rate"]),
    _m("decline_rate", "Decline rate", "percent", "Share of transactions that were declined.",
       measure=Measure(agg="avg", field="transactions.is_declined")),
    _m("chargeback_rate", "Chargeback rate", "percent", "Share of transactions with a chargeback.",
       "lower", measure=Measure(agg="avg", field="transactions.is_chargeback")),
    _m("card_count", "Cards", "count", "Number of cards with at least one transaction.",
       measure=Measure(agg="count_distinct", field="transactions.card_id")),
]


def seed(fg, *, source_id: str = "duckdb_demo", dialect: str = "duckdb",
         schema: str = "main") -> None:
    seed_model(fg, DATASETS,
               [R("transactions", "merchants", "merchant_id"), R("transactions", "cards", "card_id")],
               METRICS, source_id=source_id, dialect=dialect, schema=schema)


DEMO = Demo(
    name="fraud",
    title="Card transaction fraud",
    placeholder="Where is the fraud rate highest?",
    keys={"transactions": ("txn_id",), "merchants": ("merchant_id",), "cards": ("card_id",)},
    seed=seed,
    tables=_tables,
    generate=generate,
    definition=Definition(
        metric="fraud_loss",
        filter=FilterSpec(member="transactions.fraud_type", op="ne", values=["Lost or stolen"]),
        definition="Money lost on approved fraud transactions, excluding lost or stolen cards.",
        title="Fraud loss: exclude lost or stolen cards",
        reason="Lost or stolen cards move to the card-replacement cost metric.",
    ),
    questions=Path(__file__).parent.parent / "evals" / "questions_fraud.yaml",
    starters=[
        "What is the fraud rate by merchant category?",
        "How does cross-border fraud compare with domestic?",
        "Which card types miss the fraud-rate target?",
    ],
)
