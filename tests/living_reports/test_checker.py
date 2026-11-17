from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.checker.run import CheckContext, deterministic_pass, run_checks
from living_reports.store import get_store
from living_reports.reports.editions import EditionStore
from living_reports.reports.store import ReportStore
from living_reports.spec.models import Prose

from .conftest import friendly_payload

ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels", "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}


@pytest.fixture
def edition(orders_file, fake_llm):
    with TestClient(create_app()) as c:
        fake_llm.push(ROUTED, WORDS)
        rid = c.post("/api/ask", json={"question": "Where?"}).json()["report_id"]
    conn = get_store()
    return EditionStore(conn, ReportStore(conn)).freeze(rid)


@pytest.fixture
def ctx(edition):
    return CheckContext(graph=get_store().graph)


def status(results, step):
    return next(r.status for r in results if r.step == step)


def change_fact_value(e):
    fact = next(f for f in e.facts.values() if f.id.startswith("q1.fraud_loss."))
    e.facts[fact.id] = fact.model_copy(update={"value": fact.value + 1, "formatted": "$9.9M"})
    return e


def change_only_the_text(e):
    fact = e.facts["q1_total.fraud_loss"]
    e.facts[fact.id] = fact.model_copy(update={"formatted": "$9.9M"})
    return e


def drop_query_hash(e):
    fact = e.facts["q1_total.fraud_loss"]
    e.facts[fact.id] = fact.model_copy(update={"query_hash": ""})
    return e


def add_invented_fact(e):
    fact = e.facts["q1_total.fraud_loss"]
    e.facts["made.up"] = fact.model_copy(update={"id": "made.up"})
    return e


def use_old_metric_version(e):
    fact = e.facts["q1_total.fraud_loss"]
    e.facts[fact.id] = fact.model_copy(update={"metric_version": 99})
    return e


def remove_breakdown_row(e):
    e.queries["q1"].rows.pop()
    return e


def change_snapshot_coverage(e):
    e.coverage["rows"] += 5
    return e


def change_a_derived_fact(e):
    fact = next(f for f in e.facts.values() if ".derived." in f.id)
    e.facts[fact.id] = fact.model_copy(update={"value": (fact.value or 0) * 2 + 1})
    return e


def add_banned_phrase_in_a_claim(e):
    claim = next(iter(e.spec.claims.values()))
    e.spec.claims[claim.id] = claim.model_copy(update={"text": "This result is guaranteed."})
    e.spec.sections[0].blocks.append(Prose(text=f"{{claim:{claim.id}}}."))
    return e


def add_banned_phrase(e):
    e.spec.sections[0].blocks.append(Prose(text="This result is guaranteed."))
    return e


@pytest.mark.parametrize("plant,step", [
    (change_fact_value, "trace"),
    (change_only_the_text, "trace"),
    (drop_query_hash, "trace"),
    (add_invented_fact, "trace"),
    (use_old_metric_version, "rules"),
    (remove_breakdown_row, "totals"),
    (change_snapshot_coverage, "totals"),
    (add_banned_phrase, "rules"),
    (change_a_derived_fact, "trace"),
    (add_banned_phrase_in_a_claim, "rules"),
])
def test_planted_error_is_found(edition, ctx, plant, step):
    results = run_checks(plant(edition), ctx)
    assert status(results, step) == "fail"
    assert not deterministic_pass(results)


def test_clean_edition_passes(edition, ctx):
    results = run_checks(edition, ctx)
    assert {r.step: r.status for r in results} == {"trace": "pass", "totals": "pass", "rules": "pass"}
    assert deterministic_pass(results)


def test_findings_name_the_problem(edition, ctx):
    results = run_checks(change_only_the_text(edition), ctx)
    trace = next(r for r in results if r.step == "trace")
    assert trace.findings and "q1_total.fraud_loss" in trace.findings[0].where


def test_a_later_definition_does_not_fail_an_old_edition(edition, ctx):
    ctx.graph.put("fraud_loss", "MetricBinding", friendly_payload(get_store()))
    assert status(run_checks(edition, ctx), "rules") == "pass"


def test_checker_ignores_the_result_cache(edition, ctx):
    get_store().execute("update query_cache set payload = replace(payload, 'App', 'Fake')")
    assert status(run_checks(edition, ctx), "trace") == "pass"
