import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def test_fixture_report_payload(client):
    r = client.get("/api/reports/fraud-overview")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "draft" and body["edition"] is None
    assert body["spec"]["objective"] == "why"
    assert body["facts"]["loss.fraud_loss"]["state"] == "ok"


def test_queries_have_chart_points(client):
    body = client.get("/api/reports/fraud-overview").json()
    points = body["queries"]["rate_by_channel"]["points"]
    assert points and {"label", "value", "formatted", "fact_id"} <= set(points[0])
    assert all(p["fact_id"] in body["facts"] for p in points)


def test_points_are_sorted_as_the_query_returned(client):
    body = client.get("/api/reports/fraud-overview").json()
    values = [p["value"] for p in body["queries"]["rate_by_channel"]["points"]]
    assert values == sorted(values, reverse=True)


def test_spec_passes_validation(client):
    from living_reports.fixtures.fraud_overview import build
    from living_reports.spec.validate import validate_spec
    report = client.app.state.build_fixture()
    assert validate_spec(report.spec, report.facts, report.queries) == []
    assert build is not None


def test_unknown_report_is_404(client):
    assert client.get("/api/reports/nope").status_code == 404


def test_health(client):
    assert client.get("/api/health").json() == {
        "status": "ok", "store": "ok", "sources": {"duckdb_demo": "ok"}}


@pytest.mark.demo_data
def test_real_payload_shows_the_demo_total(real_store):
    with TestClient(create_app()) as c:
        body = c.get("/api/reports/fraud-overview").json()
    assert body["facts"]["loss.fraud_loss"]["formatted"] == "$52.6M"
