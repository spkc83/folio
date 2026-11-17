"""Home routes: config, certified, search, digest."""
import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def test_digest_route_shape(client, fake_llm):
    body = client.get("/api/digest").json()
    assert set(body) == {"since", "until", "top_changes", "suggested_questions"}
    assert body["top_changes"] == [] and body["suggested_questions"] == []
    fake_llm.push(*[{"claims": []}] * 2)
    client.post("/api/sources/duckdb_demo/advance")
    body = client.get("/api/digest").json()
    assert body["top_changes"][0]["kind"] == "definition" and body["top_changes"][0]["is_new"]
    assert any(q["question"] == "Fraud loss for Marketplace" for q in body["suggested_questions"])


ORDERS = {"placeholder": "Where is fraud loss concentrated?", "starters": [],
          "dataset": "orders", "datasets": []}


def test_config_says_demo(client, monkeypatch):
    assert client.get("/api/config").json() == {"demo": True, **ORDERS}
    monkeypatch.setenv("LR_DEMO_MODE", "false")
    # The flag is readable without a demo user: the app must know to hide the controls.
    assert client.get("/api/config").json() == {"demo": False, **ORDERS}


def test_config_gives_the_demo_s_example_questions(client, monkeypatch):
    monkeypatch.setenv("LR_DEMO", "retail")
    body = client.get("/api/config").json()
    assert body["placeholder"] == "Which categories drive the most revenue?"
    assert "What is the gross margin by category?" in body["starters"]


def test_a_deployment_with_a_fixed_data_folder_refuses_a_dataset_switch(client):
    # The client's LR_DATA_DIR is fixed, so two demos would share one store.
    response = client.post("/api/demo", json={"name": "hr"})
    assert response.status_code == 409
    assert response.json()["detail"] == "This deployment uses one fixed dataset."
    assert client.get("/api/config").json()["dataset"] == "orders"
