"""A movement question by a dimension leads with the member that moved most.

The insight eval: "How is revenue changing by channel?" gave a section by channel and month.
Its two leading claims were the trend of the total and the top channel's level, so no text
named the channel that grew. The story was that one channel's move.
"""
import pytest

from living_reports.engine import Query
from living_reports.harness.builder import AskContext, run_section
from living_reports.harness.claims import claims_for
from living_reports.reports.store import ReportStore

CHANNEL = "orders.sales_channel"
BY_CHANNEL_MONTHLY = Query(metrics=["fraud_loss"], dimensions=[CHANNEL], time={"grain": "month"})


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def _claims(ctx, question, query=BY_CHANNEL_MONTHLY):
    results, facts = run_section(query, "q1", ctx)
    out = claims_for(query, "q1", results, facts, question)
    return out.claims, out.facts({f.id: f for f in facts})


def _member_moves(claims, facts):
    return [c for c in claims if c.kind == "change" and CHANNEL in c.segment
            and not c.id.endswith(".partial") and facts[c.params["pct"]].value is not None]


def test_a_movement_question_by_a_dimension_leads_with_the_largest_member_move(ctx):
    claims, facts = _claims(ctx, "How is fraud loss changing by sales channel?")
    moves = _member_moves(claims, facts)
    assert moves, "the section has member changes"
    largest = max(moves, key=lambda c: abs(facts[c.params["pct"]].value))
    assert claims[0].id == largest.id


def test_a_question_with_no_movement_word_keeps_its_order(ctx):
    claims, _ = _claims(ctx, "Show fraud loss by sales channel and month.")
    assert claims[0].kind != "change" or CHANNEL not in claims[0].segment


def test_a_named_member_is_not_replaced_by_another_member(ctx):
    """"How is App changing?" names App: App's own claims lead, not the largest mover."""
    claims, _ = _claims(ctx, "How is fraud loss for App changing by sales channel?")
    assert claims[0].segment.get(CHANNEL) in (None, "App")
