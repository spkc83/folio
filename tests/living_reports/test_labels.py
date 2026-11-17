"""Values in the words people read: ``display_value``."""
from datetime import date, datetime

import pytest

from living_reports.engine import Query, TimeRange
from living_reports.metrics.runner import run_query, share_facts, to_facts
from living_reports.semantic.labels import display_value


@pytest.mark.parametrize("member, value, text", [
    ("orders.fraud_type", "bust_out", "Bust-out"),
    ("orders.fraud_type", "dealer_collusion", "Dealer collusion"),
    ("orders.fraud_type", "identity_theft", "Identity theft"),
    ("orders.fraud_type", "none", "No fraud"),
    ("dealers.region", "West", "West"),
    ("orders.sales_channel", "App", "App"),
    ("orders.tenure_band", "3Y+", "3Y+"),
    ("orders.order_created_date:month", "2024-01-01", "Jan 2024"),
    ("orders.order_created_date:month", date(2025, 11, 1), "Nov 2025"),
    ("orders.order_created_date:month", datetime(2025, 11, 1), "Nov 2025"),
    ("month", "2024-01-01", "Jan 2024"),
    ("orders.order_created_date:quarter", "2024-04-01", "Q2 2024"),
    ("orders.order_created_date:year", "2024-01-01", "2024"),
    ("orders.order_created_date:day", "2025-11-30", "30 Nov 2025"),
    ("orders.order_created_date:week", "2025-11-24", "Week of 24 Nov 2025"),
    ("orders.sales_channel", None, "No value"),
    ("orders.num_items", 3, "3"),
])
def test_display_value(member, value, text):
    assert display_value(member, value) == text


def test_a_model_value_label_wins():
    assert display_value("orders.fraud_type", "none", {"none": "Not fraud"}) == "Not fraud"
    assert display_value("orders.fraud_type", "bust_out", {"none": "Not fraud"}) == "Bust-out"


def test_fact_labels_use_display_values(store):
    q = Query(metrics=["fraud_loss"], dimensions=["orders.fraud_type"],
              time=TimeRange(grain="month", start=date(2024, 1, 1), end=date(2024, 2, 1)))
    result = run_query(q, "duckdb_demo", store)
    labels = {f.label for f in to_facts(result, "q1")}
    assert "Fraud loss · Identity theft · Jan 2024" in labels
    assert "Fraud loss · No fraud · Jan 2024" in labels
    assert not any("_" in label or "2024-01" in label for label in labels)
    shares = {f.label for f in share_facts(result, "fraud_loss", "q1")}
    assert "Share of fraud loss · Identity theft · Jan 2024" in shares


def test_fact_ids_and_keys_keep_raw_values(store):
    q = Query(metrics=["fraud_loss"], dimensions=["orders.fraud_type"])
    facts = to_facts(run_query(q, "duckdb_demo", store), "q1")
    fact = next(f for f in facts if f.binding.keys["orders.fraud_type"] == "identity_theft")
    assert fact.id == "q1.fraud_loss.identity_theft"
