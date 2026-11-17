"""The retail sales demo: about 400,000 sale lines in 120 stores, January 2024 to December 2025.

Planted stories (the tests in ``test_demo_retail.py`` hold each one to a range):

1. Online revenue grows about 60% from 2024 to 2025; Store revenue is flat.
2. Electronics gross margin falls from about 30% to about 22% after a discount push from
   July 2025.
3. The Apparel return rate in the North rises to about 18% in Q4 2025 (others 4-11%).
4. November and December revenue is about 1.6 times an average month.
5. Outlet stores miss the 35% margin target in every quarter.
6. A new channel, Social commerce, starts in December 2025 (it arrives with the new data).
"""
from pathlib import Path

import duckdb

from living_reports.semantic.kinds import FilterSpec, Measure, MetricBindingPayload, Ratio

from . import Definition, Demo, moment_tables, u, write_parquet
from ._model import D, F, R, seed_model

ROWS = 400_000
STORES = 120
PRODUCTS = 600


def generate(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"""
    CREATE TABLE stores AS
    SELECT 'S' || lpad((i + 1)::VARCHAR, 3, '0') AS store_id,
           ['North', 'South', 'East', 'West'][1 + floor({u('i', 'region')} * 4)::INT] AS region,
           CASE WHEN {u('i', 'format')} < 0.10 THEN 'Flagship'
                WHEN {u('i', 'format')} < 0.75 THEN 'Standard' ELSE 'Outlet' END AS store_format
    FROM range({STORES}) t(i)""")
    con.execute(f"""
    CREATE TABLE products AS
    SELECT 'P' || lpad((i + 1)::VARCHAR, 4, '0') AS product_id,
           CASE WHEN c < 0.30 THEN 'Apparel' WHEN c < 0.45 THEN 'Electronics'
                WHEN c < 0.65 THEN 'Home' WHEN c < 0.80 THEN 'Beauty' ELSE 'Grocery' END AS category,
           'Brand ' || chr(65 + floor({u('i', 'brand')} * 8)::INT) AS brand,
           0.5 + {u('i', 'price')} AS price_factor
    FROM (SELECT i, {u('i', 'category')} AS c FROM range({PRODUCTS}) t(i))""")
    # Day weights: the holiday peak, 17% more trade in 2025, and busier weekends.
    con.execute("""
    CREATE TABLE days AS
    SELECT d::DATE AS day,
           sum(w) OVER (ORDER BY d) / sum(w) OVER () AS c
    FROM (SELECT d,
                 (CASE month(d) WHEN 11 THEN 1.7 WHEN 12 THEN 2.0 ELSE 1.0 END)
                 * (CASE WHEN year(d) = 2025 THEN 1.17 ELSE 1.0 END)
                 * (CASE dayofweek(d) WHEN 6 THEN 1.25 WHEN 0 THEN 1.15 ELSE 1.0 END) AS w
          FROM range(TIMESTAMP '2024-01-01', TIMESTAMP '2026-01-01', INTERVAL 1 DAY) t(d))""")
    con.execute(f"""
    CREATE TABLE lines AS
    SELECT b.*, d.day
    FROM (SELECT i, {u('i', 'day')} AS u_day FROM range({ROWS}) t(i)) b
    ASOF JOIN days d ON d.c >= b.u_day""")
    con.execute(f"""
    CREATE TABLE sales AS
    WITH x AS (
      SELECT l.i, l.day, p.product_id, p.category, p.price_factor, s.store_id, s.region,
             s.store_format,
             {u('l.i', 'channel')} AS u_channel, {u('l.i', 'units')} AS u_units,
             {u('l.i', 'discount')} AS u_discount, {u('l.i', 'cost')} AS u_cost,
             {u('l.i', 'return')} AS u_return, {u('l.i', 'segment')} AS u_segment,
             {u('l.i', 'promo')} AS u_promo, {u('l.i', 'hour')} AS u_hour
      FROM lines l
      JOIN stores s ON s.store_id = 'S' || lpad((1 + floor({u('l.i', 'store')} * {STORES})::INT)::VARCHAR, 3, '0')
      JOIN products p ON p.product_id = 'P' || lpad((1 + floor({u('l.i', 'product')} * {PRODUCTS})::INT)::VARCHAR, 4, '0')
    ), priced AS (
      SELECT *,
        CASE WHEN day >= DATE '2025-12-01' AND u_channel >= 0.96 THEN 'Social commerce'
             WHEN u_channel < CASE WHEN year(day) = 2025 THEN 0.342 ELSE 0.25 END THEN 'Online'
             WHEN u_channel < CASE WHEN year(day) = 2025 THEN 0.462 ELSE 0.37 END THEN 'Click & collect'
             ELSE 'Store' END AS channel,
        CASE category WHEN 'Grocery' THEN 1 + floor(u_units * 6)
                      WHEN 'Electronics' THEN 1 + floor(u_units * u_units * 2)
                      ELSE 1 + floor(u_units * 3) END::INT AS units,
        CASE category WHEN 'Apparel' THEN 45 WHEN 'Electronics' THEN 320 WHEN 'Home' THEN 85
                      WHEN 'Beauty' THEN 28 ELSE 12 END * price_factor AS price,
        CASE category WHEN 'Apparel' THEN 0.50 WHEN 'Electronics' THEN 0.64 WHEN 'Home' THEN 0.55
                      WHEN 'Beauty' THEN 0.45 ELSE 0.70 END AS cost_share,
        greatest(0.0,
          CASE category WHEN 'Electronics' THEN 0.08 WHEN 'Beauty' THEN 0.08 ELSE 0.10 END
          + CASE WHEN store_format = 'Outlet' THEN 0.12 ELSE 0.0 END
          + CASE WHEN category = 'Electronics' AND day >= DATE '2025-07-01' THEN 0.10 ELSE 0.0 END
          + (u_discount - 0.5) * 0.04) AS discount_share,
        CASE WHEN region = 'North' AND category = 'Apparel' AND day >= DATE '2025-10-01' THEN 0.18
             ELSE CASE category WHEN 'Apparel' THEN 0.08 WHEN 'Electronics' THEN 0.06
                                WHEN 'Home' THEN 0.05 WHEN 'Beauty' THEN 0.04 ELSE 0.01 END
                  + CASE WHEN u_channel < 0.30 THEN 0.02 ELSE 0.0 END END AS return_p
      FROM x
    ), valued AS (
      SELECT *, round(price * units, 2) AS list_value FROM priced
    )
    SELECT
      'SL-' || lpad((row_number() OVER (ORDER BY day, i))::VARCHAR, 7, '0') AS sale_id,
      day + INTERVAL (8 + floor(u_hour * 13)::INT) HOUR
          + INTERVAL (floor(u_hour * 7919)::INT % 60) MINUTE AS sale_date,
      store_id, product_id, channel,
      CASE WHEN u_segment < 0.25 THEN 'New' WHEN u_segment < 0.70 THEN 'Returning'
           ELSE 'Loyalty' END AS customer_segment,
      units,
      list_value,
      round(list_value * discount_share, 2) AS discount,
      round(list_value - round(list_value * discount_share, 2), 2) AS revenue,
      round(list_value * cost_share * (0.97 + 0.06 * u_cost), 2) AS cost,
      round(list_value - round(list_value * discount_share, 2)
            - round(list_value * cost_share * (0.97 + 0.06 * u_cost), 2), 2) AS gross_profit,
      (u_return < return_p)::INT AS is_returned,
      CASE WHEN month(day) IN (3, 4) AND u_promo < 0.3 THEN 'SPRING'
           WHEN month(day) IN (7, 8) AND u_promo < 0.3 THEN 'SUMMER'
           WHEN month(day) = 11 AND day(day) >= 20 AND u_promo < 0.6 THEN 'BLACKFRIDAY'
           WHEN month(day) = 12 AND u_promo < 0.5 THEN 'XMAS'
           ELSE 'None' END AS promotion_code
    FROM valued
    ORDER BY sale_date""")
    write_parquet(con, folder, {"sales": "*", "stores": "*",
                                "products": "product_id, category, brand"})


def _tables(folder: Path, moment: int) -> dict[str, str]:
    return moment_tables(folder, moment, "sales", "sale_date", "promotion_code",
                         ["stores", "products"])


DATASETS = {
    "sales": D("sale_id", {
        "sale_id": F("string"),
        "sale_date": F("datetime", "Sale date", time=True),
        "store_id": F("string"),
        "product_id": F("string"),
        "channel": F("string", "Channel", synonyms=["sales channel"]),
        "customer_segment": F("string", "Customer segment", synonyms=["segment"]),
        "units": F("integer"),
        "list_value": F("decimal"),
        "discount": F("decimal"),
        "revenue": F("decimal"),
        "cost": F("decimal"),
        "gross_profit": F("decimal"),
        "is_returned": F("integer"),
    }, time_field="sale_date"),
    "stores": D("store_id", {
        "store_id": F("string"),
        "region": F("string", "Region"),
        "store_format": F("string", "Store format", synonyms=["format"]),
    }),
    "products": D("product_id", {
        "product_id": F("string"),
        "category": F("string", "Category", synonyms=["product category"]),
        "brand": F("string", "Brand"),
    }),
}


def _m(name, label, fmt, definition, better=None, target=None, *, measure=None, ratio=None,
       synonyms=()):
    return MetricBindingPayload(name=name, label=label, format=fmt, definition=definition,
                                measure=measure, ratio=ratio, better=better, target=target,
                                synonyms=list(synonyms))


METRICS = [
    _m("revenue", "Revenue", "currency", "Sales value after discounts.", "higher",
       measure=Measure(agg="sum", field="sales.revenue"), synonyms=["sales"]),
    _m("units", "Units", "count", "Number of items sold.", "higher",
       measure=Measure(agg="sum", field="sales.units")),
    _m("sale_lines", "Sale lines", "count", "Number of sale lines.",
       measure=Measure(agg="count", dataset="sales")),
    _m("gross_profit", "Gross profit", "currency", "Revenue minus the cost of the goods sold.",
       "higher", measure=Measure(agg="sum", field="sales.gross_profit")),
    _m("gross_margin", "Gross margin", "percent", "Gross profit as a share of revenue.",
       "higher", 0.35, ratio=Ratio(numerator="gross_profit", denominator="revenue"),
       synonyms=["margin"]),
    _m("return_rate", "Return rate", "percent", "Share of sale lines that were returned.",
       "lower", 0.08, measure=Measure(agg="avg", field="sales.is_returned"),
       synonyms=["returns"]),
    _m("discounts", "Discounts", "currency", "Value of the discounts given.", "lower",
       measure=Measure(agg="sum", field="sales.discount")),
    _m("list_sales", "Sales before discounts", "currency", "Sales value at list price.",
       measure=Measure(agg="sum", field="sales.list_value")),
    _m("discount_rate", "Discount rate", "percent", "Discounts as a share of list-price sales.",
       "lower", ratio=Ratio(numerator="discounts", denominator="list_sales")),
    _m("average_sale_value", "Average sale value", "currency", "Revenue of an average sale line.",
       "higher", measure=Measure(agg="avg", field="sales.revenue")),
    _m("store_count", "Stores", "count", "Number of stores with at least one sale.",
       measure=Measure(agg="count_distinct", field="sales.store_id")),
]


def seed(fg, *, source_id: str = "duckdb_demo", dialect: str = "duckdb",
         schema: str = "main") -> None:
    seed_model(fg, DATASETS,
               [R("sales", "stores", "store_id"), R("sales", "products", "product_id")],
               METRICS, source_id=source_id, dialect=dialect, schema=schema)


DEMO = Demo(
    name="retail",
    title="Retail sales",
    placeholder="Which categories drive the most revenue?",
    keys={"sales": ("sale_id",), "stores": ("store_id",), "products": ("product_id",)},
    seed=seed,
    tables=_tables,
    generate=generate,
    definition=Definition(
        metric="revenue",
        filter=FilterSpec(member="sales.is_returned", op="eq", values=[0]),
        definition="Sales value after discounts, excluding returned sales.",
        title="Revenue: exclude returned sales",
        reason="Finance reports revenue net of returns.",
    ),
    questions=Path(__file__).parent.parent / "evals" / "questions_retail.yaml",
    starters=[
        "How is online revenue growing?",
        "What is the gross margin by category?",
        "Which regions have the highest return rate?",
    ],
)
