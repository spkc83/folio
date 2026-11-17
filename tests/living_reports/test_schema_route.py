"""The schema of a source on the home page: what a person can ask about, and the columns."""
import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app

USER = {"X-LR-User": "ana"}


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def _schema(client):
    r = client.get("/api/sources/duckdb_demo/schema", headers=USER)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_schema_names_the_terms_a_person_can_ask_about(client):
    body = _schema(client)
    assert body["source_id"] == "duckdb_demo"
    assert "fraud_loss" in {m["id"] for m in body["metrics"]}
    dims = {d["id"]: d for d in body["dimensions"]}
    assert "orders.sales_channel" in dims
    assert dims["orders.order_created_date"]["is_time"] is True


def test_each_dataset_lists_its_modeled_columns_with_a_profile(client):
    orders = {d["name"]: d for d in _schema(client)["datasets"]}["orders"]
    assert orders["row_count"] == 2800  # the demo starts at moment 1
    cols = {c["name"]: c for c in orders["columns"]}
    channel = cols["sales_channel"]
    assert channel["field_id"] == "orders.sales_channel"
    assert channel["label"] == "Sales channel"
    assert channel["type"] == "string"
    assert channel["dimension"] is True
    assert channel["distinct_count"] == 3
    assert set(channel["samples"]) <= {"App", "Telesales", "Web"}
    assert channel["samples"]


def test_the_time_column_shows_its_range(client):
    orders = {d["name"]: d for d in _schema(client)["datasets"]}["orders"]
    date = {c["name"]: c for c in orders["columns"]}["order_created_date"]
    assert date["min"].startswith("2024-01-01")
    assert date["max"].startswith("2025-11-30")


def test_a_column_outside_the_model_is_not_shown(client):
    """Only modeled columns: the panel shows what Folio can answer, nothing else."""
    orders = {d["name"]: d for d in _schema(client)["datasets"]}["orders"]
    assert "phone_line_type" not in {c["name"] for c in orders["columns"]}


def test_an_unknown_source_is_404(client):
    assert client.get("/api/sources/nope/schema", headers=USER).status_code == 404
