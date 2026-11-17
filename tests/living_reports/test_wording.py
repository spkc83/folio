import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.checker.run import CheckContext, deterministic_pass, run_all
from living_reports.checker.steps import CheckResult
from living_reports.checker.wording import challenge_wording
from living_reports.store import get_store
from living_reports.reports.editions import EditionStore
from living_reports.reports.store import ReportStore

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Telesales drives the loss", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}


@pytest.fixture
def edition(orders_file, fake_llm, monkeypatch):
    monkeypatch.setenv("LR_CHECKER_MODEL", "checker-model")
    monkeypatch.setenv("LR_SMALL_MODEL", "author-model")
    with TestClient(create_app()) as c:
        fake_llm.push(ROUTED, WORDS)
        rid = c.post("/api/ask", json={"question": "Where?"}).json()["report_id"]
    conn = get_store()
    return EditionStore(conn, ReportStore(conn)).freeze(rid)


def test_causal_claim_gives_open(edition, fake_llm):
    fake_llm.push({"claims": [{"text": "Telesales drives the loss", "issue": "This states a cause."}]})
    r = challenge_wording(edition)
    assert r.status == "open" and "drives" in r.findings[0].where


def test_no_claims_gives_pass(edition, fake_llm):
    fake_llm.push({"claims": []})
    assert challenge_wording(edition).status == "pass"


def test_unavailable_model_gives_open_not_pass(edition, fake_llm):
    fake_llm.push(None)
    r = challenge_wording(edition)
    assert r.status == "open" and "not available" in r.findings[0].detail


def test_invalid_model_output_gives_open(edition, fake_llm):
    fake_llm.push("x", "y", "z")
    assert challenge_wording(edition).status == "open"


def test_same_model_as_the_author_is_recorded(edition, fake_llm, monkeypatch):
    monkeypatch.setenv("LR_CHECKER_MODEL", "author-model")
    fake_llm.push({"claims": []})
    r = challenge_wording(edition)
    assert r.status == "open" and "same model" in r.findings[0].detail


def test_wording_never_fails_a_report(edition, fake_llm):
    fake_llm.push({"claims": [{"text": "a", "issue": "b"}]})
    assert challenge_wording(edition).status != "fail"


def test_wording_pass_cannot_rescue_a_failed_check():
    results = [CheckResult(step="trace", status="fail"), CheckResult(step="wording", status="pass")]
    assert not deterministic_pass(results)


def test_run_all_adds_the_wording_step(edition, fake_llm):
    fake_llm.push({"claims": []})
    steps = [r.step for r in run_all(edition, CheckContext(graph=get_store().graph))]
    assert steps == ["trace", "totals", "rules", "wording"]


def test_model_text_is_treated_as_data(edition, fake_llm):
    fake_llm.push({"claims": []})
    challenge_wording(edition)
    assert "<report>" in fake_llm.prompts[-1]
