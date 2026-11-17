"""Search over report titles and fact labels: one SQL statement, LIKE wildcards escaped."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.metrics.runner import Fact
from living_reports.reports.search import escape_like, search_reports
from living_reports.reports.store import ReportStore, StoredReport
from living_reports.spec.models import Prose, ReportSpec, Section

T0 = datetime(2026, 11, 1, 9, 0)


def report(title: str, labels: list[str], tier: str = "team") -> StoredReport:
    facts = {
        f"q1.f{i}": Fact(id=f"q1.f{i}", label=label, value=1.0, formatted="1",
                        query_hash="h", source_id="duckdb_demo")
        for i, label in enumerate(labels)
    }
    spec = ReportSpec(title=title, objective="why", sections=[
        Section(id="s1", heading="H", intent="Q", blocks=[Prose(text="Text.")])])
    return StoredReport(author="ana", tier=tier, source_id="duckdb_demo", view="narrative", spec=spec,
                        facts=facts, queries={}, query_defs={})


@pytest.fixture
def ticking(store):
    clock = iter(T0 + timedelta(minutes=i) for i in range(1000))
    store.clock = lambda: next(clock)
    return store


@pytest.fixture
def saved(ticking):
    reports = ReportStore(ticking)
    loss = reports.save(report("Loss by channel", ["Fraud loss", "Fraud loss · Telesales"]))
    orders = reports.save(report("Orders by region", ["Orders · East"]))
    return loss.id, orders.id


def test_search_matches_titles_and_fact_labels(ticking, saved):
    loss, orders = saved
    [hit] = search_reports(ticking, "TeleSales")
    assert hit.report_id == loss and hit.title == "Loss by channel" and hit.tier == "team"
    assert hit.labels == ["Fraud loss · Telesales"]
    assert [h.report_id for h in search_reports(ticking, "by")] == [orders, loss]
    assert [h.labels for h in search_reports(ticking, "by")] == [[], []]
    [hit] = search_reports(ticking, "loss")
    assert hit.report_id == loss and hit.labels == ["Fraud loss", "Fraud loss · Telesales"]


def test_search_escapes_like_wildcards(ticking, saved):
    pct = ReportStore(ticking).save(report("Match rate", ["100% match"])).id
    assert [h.report_id for h in search_reports(ticking, "%")] == [pct]
    assert search_reports(ticking, "_") == []
    assert search_reports(ticking, "\\") == []
    assert escape_like("A%_\\b") == "a\\%\\_\\\\b"


def test_search_blank_and_too_long(ticking, saved, monkeypatch):
    assert search_reports(ticking, "   ") == []
    assert search_reports(ticking, "") == []
    with TestClient(create_app()) as client:
        assert client.get("/api/search", params={"q": ""}).json() == []
        assert client.get("/api/search", params={"q": "   "}).json() == []
        [hit] = client.get("/api/search", params={"q": "telesales"}).json()
        assert hit["labels"] == ["Fraud loss · Telesales"]
        assert client.get("/api/search", params={"q": "x" * 200}).status_code == 200
        assert client.get("/api/search", params={"q": "x" * 201}).status_code == 422


def test_blank_search_runs_no_query(ticking, monkeypatch):
    def fail(*_):
        raise AssertionError("a blank term must not query")
    monkeypatch.setattr(ticking, "rows", fail)
    assert search_reports(ticking, " \t ") == []


def test_search_caps_labels_at_three_and_reports_at_limit(ticking):
    reports = ReportStore(ticking)
    many = reports.save(report("Wide", [f"Loss {i}" for i in range(6)] + ["Loss 0"])).id
    [hit] = search_reports(ticking, "loss")
    assert hit.report_id == many and hit.labels == ["Loss 0", "Loss 1", "Loss 2"]
    for i in range(4):
        reports.save(report(f"Loss report {i}", []))
    hits = search_reports(ticking, "loss", limit=3)
    assert [h.title for h in hits] == ["Loss report 3", "Loss report 2", "Loss report 1"]
    assert len(search_reports(ticking, "loss")) == 5
