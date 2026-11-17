"""The insight eval: a planted story counts as surfaced when a claim matches its rule.

The rules read a claim's kind, its segment, its metric, and a direction that code computes
from the claim's facts, never from its words.
"""
from datetime import date
from pathlib import Path

import pytest

from living_reports.engine import Query, TimeRange
from living_reports.evals.insights import Story, StoryRule, load_insights, matches, surfaced
from living_reports.harness.claims import claims_for
from living_reports.metrics.runner import to_facts

from .test_claims import MONTH, result
from .test_claims_direction import RATE, channel_case

EVALS = Path(__file__).parents[2] / "living_reports" / "evals"


def _claims():
    query, results, facts = channel_case()
    out = claims_for(query, "q1", results, facts)
    return out.claims, out.facts(facts)


def test_an_extreme_matches_up_for_the_top_member_only():
    claims, facts = _claims()
    top = StoryRule(kinds=["extreme"], metric="fraud_rate",
                    segment={"orders.sales_channel": "Telesales"}, direction="up")
    bottom = top.model_copy(update={"segment": {"orders.sales_channel": "Retail"}})
    assert any(matches(top, c, facts) for c in claims)
    assert not any(matches(bottom, c, facts) for c in claims)  # Retail is the lowest, not the highest


def test_a_target_rule_needs_a_miss():
    claims, facts = _claims()
    miss = StoryRule(kinds=["target"], metric="fraud_rate",
                     segment={"orders.sales_channel": "Telesales"}, misses=True)
    meet = miss.model_copy(update={"segment": {"orders.sales_channel": "Retail"}})
    assert any(matches(miss, c, facts) for c in claims)  # 3.50% misses the 3.00% target
    assert not any(matches(meet, c, facts) for c in claims)  # 2.36% meets it


def test_the_metric_and_the_kind_must_match():
    claims, facts = _claims()
    other_metric = StoryRule(kinds=["extreme"], metric="fraud_loss",
                             segment={"orders.sales_channel": "Telesales"}, direction="up")
    other_kind = StoryRule(kinds=["change"], metric="fraud_rate",
                           segment={"orders.sales_channel": "Telesales"}, direction="up")
    assert not any(matches(r, c, facts) for r in (other_metric, other_kind) for c in claims)


def test_a_compare_names_the_larger_member():
    claims, facts = _claims()
    rule = StoryRule(kinds=["compare"], metric="fraud_rate", compare_a="Telesales")
    assert any(matches(rule, c, facts) for c in claims)
    assert not any(matches(rule.model_copy(update={"compare_a": "Partner"}), c, facts) for c in claims)


def _series(values):
    query = Query(metrics=["fraud_rate"],
                  time=TimeRange(start=date(2025, 1, 1), end=date(2025, 7, 1), grain="month"))
    months = [f"2025-0{m}-01" for m in range(1, 7)]
    main = result([MONTH, "fraud_rate"], [[m, v] for m, v in zip(months, values)], [MONTH], meta=RATE)
    facts = to_facts(main, "q1")
    out = claims_for(query, "q1", {"q1": main}, facts)
    return out.claims, out.facts(facts)


@pytest.mark.parametrize("values, up, down", [
    ([0.01, 0.012, 0.014, 0.016, 0.018, 0.020], True, False),
    ([0.020, 0.018, 0.016, 0.014, 0.012, 0.010], False, True),
])
def test_a_change_and_a_trend_get_their_direction_from_the_facts(values, up, down):
    claims, facts = _series(values)
    rule = StoryRule(kinds=["change", "trend"], metric="fraud_rate", direction="up")
    assert any(matches(rule, c, facts) for c in claims) is up
    assert any(matches(rule.model_copy(update={"direction": "down"}), c, facts) for c in claims) is down


def test_a_story_is_surfaced_by_any_of_its_rules_and_never_when_it_has_no_kind():
    claims, facts = _claims()
    story = Story(id="t.1", text="Telesales is the riskiest channel", any_of=[
        StoryRule(kinds=["change"], metric="fraud_rate", direction="up"),
        StoryRule(kinds=["extreme"], metric="fraud_rate",
                  segment={"orders.sales_channel": "Telesales"}, direction="up"),
    ])
    assert surfaced(story, claims, facts)
    assert not surfaced(Story(id="t.2", text="no claim kind", expressible=False), claims, facts)


@pytest.mark.parametrize("demo", ["retail", "fraud", "hr"])
def test_each_demo_has_five_stories_and_three_questions_each(demo):
    stories, questions = load_insights(EVALS / f"insights_{demo}.yaml")
    assert len(stories) == 5
    assert {q.story for q in questions} == set(stories)
    for sid in stories:
        assert sorted(q.level for q in questions if q.story == sid) == ["direct", "open", "topic"]
