from fastapi.testclient import TestClient

from living_reports.api.app import create_app


def test_header_identity_works_only_in_demo_mode(orders_file, monkeypatch):
    with TestClient(create_app()) as client:
        assert client.get("/api/reports", headers={"X-LR-User": "omar"}).status_code == 200
        monkeypatch.setenv("LR_DEMO_MODE", "false")
        r = client.get("/api/reports", headers={"X-LR-User": "omar"})
        assert r.status_code == 401 and "identity" in r.json()["detail"]


def test_health_stays_open_outside_demo_mode(orders_file, monkeypatch):
    with TestClient(create_app()) as client:
        monkeypatch.setenv("LR_DEMO_MODE", "false")
        assert client.get("/api/health").status_code == 200
