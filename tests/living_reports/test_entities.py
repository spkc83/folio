"""Entities that code finds in a question: named values, periods, and a count of items."""
from datetime import date

import pytest

from living_reports.harness.entities import find

VALUES = {
    "orders.sales_channel": ["App", "Telesales", "Web"],
    "dealers.region": ["East", "West"],
    "orders.fraud_type": ["friendly_fraud", "identity_theft", "none"],
    "orders.tenure_band": ["3Y+", "New"],
}
FIRST, LAST = date(2024, 1, 1), date(2025, 11, 30)


def found(question):
    return find(question, VALUES, FIRST, LAST)


def test_a_named_value_is_found_with_its_member():
    assert found("What is the fraud rate in the Telesales channel?").values == {
        "orders.sales_channel": ["Telesales"]
    }


def test_several_values_of_one_member_are_found_in_value_order():
    assert found("Compare fraud rate between Web and App.").values == {
        "orders.sales_channel": ["App", "Web"]
    }


def test_an_underscore_value_matches_its_words():
    assert found("What is the identity theft loss by region?").values == {
        "orders.fraud_type": ["identity_theft"]
    }


@pytest.mark.parametrize(
    "question",
    [
        "How many new orders came in?",  # "New" is a value, "new" is a common word
        "Show orders with none of the checks.",  # a stop word is never a value
        "Which application had the most loss?",  # App only as a whole word
        "What is the webinar loss?",
    ],
)
def test_common_words_and_parts_of_words_are_not_values(question):
    assert found(question).values == {}


@pytest.mark.parametrize(
    ("question", "start", "end", "text"),
    [
        ("What was the fraud loss in 2024?", date(2024, 1, 1), date(2025, 1, 1), "2024"),
        ("How many orders were placed in January 2025?", date(2025, 1, 1), date(2025, 2, 1), "January 2025"),
        ("Fraud loss in Q3 2025?", date(2025, 7, 1), date(2025, 10, 1), "Q3 2025"),
        # The data ends on 2025-11-30, the last day of a month: November is complete.
        ("Fraud loss last month?", date(2025, 11, 1), date(2025, 12, 1), "last month"),
        ("Fraud loss last quarter?", date(2025, 7, 1), date(2025, 10, 1), "last quarter"),
        ("Fraud loss last year?", date(2024, 1, 1), date(2025, 1, 1), "last year"),
        # "This" period ends where the calendar period ends, not where the data ends: a
        # certified question asked again after December arrives must count December.
        ("Fraud loss this year?", date(2025, 1, 1), date(2026, 1, 1), "this year"),
        ("Fraud loss this quarter?", date(2025, 10, 1), date(2026, 1, 1), "this quarter"),
        ("Fraud loss this month?", date(2025, 11, 1), date(2025, 12, 1), "this month"),
    ],
)
def test_an_explicit_period_becomes_a_date_range(question, start, end, text):
    period = found(question).period
    assert (period.start, period.end, period.text) == (start, end, text)


def test_a_period_outside_the_data_is_dropped():
    assert found("What was the fraud loss in 2019?").period is None


def test_two_periods_are_left_to_the_model():
    assert found("Compare fraud loss in 2024 and 2025.").period is None


def test_last_month_is_the_last_full_month_when_the_data_ends_mid_month():
    period = find("Orders last month?", VALUES, FIRST, date(2025, 11, 14)).period
    assert (period.start, period.end) == (date(2025, 10, 1), date(2025, 11, 1))


@pytest.mark.parametrize(
    ("question", "limit"),
    [
        ("Give me the top 3 channels by fraud loss.", 3),
        ("Show the 5 biggest regions.", 5),
        ("Top five channels by loss?", 5),
        ("What is the fraud loss in 2024?", None),
        ("Show the top channels.", None),
    ],
)
def test_a_count_of_items_becomes_a_limit(question, limit):
    assert found(question).limit == limit


@pytest.mark.parametrize(
    ("question", "since"),
    [
        # "after March 2025" starts with April: March is the last month before.
        ("How did the fraud rate change after March 2025?", date(2025, 4, 1)),
        ("How has fraud loss moved since March 2025?", date(2025, 3, 1)),
        ("Did the fraud rate rise after 2024?", date(2025, 1, 1)),
    ],
)
def test_after_or_since_a_period_is_a_start_date_and_not_the_period(question, since):
    f = found(question)
    assert f.since == since
    assert f.period is None  # "after March 2025" does not ask about March only
