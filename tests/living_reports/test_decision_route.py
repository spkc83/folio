"""Decision-first routing: the decision model and code build the query; the language model is the
fallback when the decision is unsure or the question needs what a decision cannot express."""
import pytest

from living_reports.harness.router import route
from living_reports.semantic.demo import seed_demo
from living_reports.store import get_store

from .test_router import draft

CHANNEL = "orders.sales_channel"
LOSS = {"metrics": ["fraud_loss"]}


@pytest.fixture
def seeded(store, orders_file):
    store = get_store()
    seed_demo(store.graph)
    return store


@pytest.fixture
def decider(monkeypatch):
    """A decision model with set probabilities for each question."""
    from living_reports.harness import decide
    monkeypatch.setenv("LR_DECIDE_MODEL", "ollama/d3:4b")

    def choice(probs):
        return {"type": "choice", "choice": max(probs, key=probs.get), "probabilities": probs}

    def post(url, body, timeout):
        decider.calls.append(body)
        answers = {"answerable": {"type": "noul", "noul": decider.answerable},
                   "metric": choice(decider.metric)}
        for key in ("breakdown", "grain"):
            if key in body["questions"]:
                answers[key] = choice(getattr(decider, key))
        return {"answers": answers}

    decider.calls, decider.answerable = [], 0.95
    decider.metric = {"fraud_loss": 0.9, "fraud_rate": 0.05, "order_count": 0.05}
    decider.breakdown = {"none": 0.9, CHANNEL: 0.05, "dealers.region": 0.05}
    decider.grain = {"none": 0.9, "month": 0.1}
    monkeypatch.setattr(decide, "_post", post)
    return decider


def ask(question, store):
    return route(question, store, "duckdb_demo", "t")


def test_a_confident_decision_routes_with_no_language_model_call(fake_llm, seeded, decider):
    decider.metric = {"fraud_rate": 0.9, "fraud_loss": 0.1}
    decider.breakdown = {CHANNEL: 0.92, "none": 0.08}
    r = ask("Show fraud rate by sales channel.", seeded)
    assert r.route == "decision" and fake_llm.prompts == []
    assert r.query.metrics == ["fraud_rate"] and r.query.dimensions == [CHANNEL]


def test_the_decision_request_asks_for_the_breakdown_and_the_grain(fake_llm, seeded, decider):
    ask("What is the total fraud loss?", seeded)
    questions = decider.calls[0]["questions"]
    assert {"none", CHANNEL, "dealers.region"} <= set(questions["breakdown"]["criteria"])
    assert "orders.order_created_date" not in questions["breakdown"]["criteria"]
    assert {"none", "month", "quarter", "year"} <= set(questions["grain"]["criteria"])


def test_top_n_takes_the_count_from_the_question(fake_llm, seeded, decider):
    decider.breakdown = {CHANNEL: 0.9, "none": 0.1}
    r = ask("Give me the top 3 channels by fraud loss.", seeded)
    assert r.route == "decision"
    assert (r.query.dimensions, r.query.limit, r.query.order) == ([CHANNEL], 3, [])


def test_the_lowest_n_sorts_up(fake_llm, seeded, decider):
    decider.breakdown = {"dealers.region": 0.9, "none": 0.1}
    r = ask("Which are the 2 lowest regions by fraud loss?", seeded)
    assert r.query.limit == 2 and r.query.order == [("fraud_loss", "asc")]


def test_code_adds_the_named_value_and_the_period(fake_llm, seeded, decider):
    decider.metric = {"order_count": 0.95, "fraud_loss": 0.05}
    r = ask("How many orders came from the East region in 2024?", seeded)
    assert r.route == "decision"
    (f,) = r.query.filters
    assert (f.member, f.op, f.values) == ("dealers.region", "eq", ["East"])
    assert (str(r.query.time.start), str(r.query.time.end)) == ("2024-01-01", "2025-01-01")


def test_a_grain_makes_a_series(fake_llm, seeded, decider):
    decider.metric = {"order_count": 0.95, "fraud_loss": 0.05}
    decider.grain = {"month": 0.93, "none": 0.07}
    r = ask("Show the monthly trend of orders.", seeded)
    assert r.query.time.grain == "month" and r.query.dimensions == []


def test_a_named_value_of_another_dimension_is_a_filter_not_a_second_breakdown(fake_llm, seeded, decider):
    decider.breakdown = {"orders.fraud_type": 0.9, "none": 0.1}
    r = ask("Should we review the Telesales channel? Show its fraud loss by fraud type.", seeded)
    assert r.route == "decision" and r.query.dimensions == ["orders.fraud_type"]
    assert [(f.member, f.values) for f in r.query.filters] == [(CHANNEL, ["Telesales"])]


def test_two_named_values_of_the_breakdown_become_a_comparison(fake_llm, seeded, decider):
    decider.metric = {"fraud_rate": 0.9, "fraud_loss": 0.1}
    decider.breakdown = {CHANNEL: 0.9, "none": 0.1}
    r = ask("Compare fraud rate between Web and App.", seeded)
    assert r.query.dimensions == [CHANNEL]
    assert [(f.op, sorted(f.values)) for f in r.query.filters] == [("in", ["App", "Web"])]


def test_a_vague_metric_becomes_the_clarify_between_the_two(fake_llm, seeded, decider):
    decider.metric = {"fraud_loss": 0.5, "fraud_rate": 0.4, "order_count": 0.1}
    decider.breakdown = {CHANNEL: 0.9, "none": 0.1}
    r = ask("How big is the problem by channel?", seeded)
    assert r.route == "decision" and fake_llm.prompts == []
    assert {o.query.metrics[0] for o in r.clarify.options} == {"fraud_loss", "fraud_rate"}


@pytest.mark.parametrize("probs", [
    {"breakdown": {CHANNEL: 0.6, "dealers.region": 0.4}},
    {"grain": {"month": 0.5, "none": 0.5}},
    {"metric": {"fraud_loss": 0.78, "fraud_rate": 0.12, "order_count": 0.1}},
    {"answerable": 0.5},
])
def test_an_unsure_decision_goes_to_the_language_model(fake_llm, seeded, decider, probs):
    for key, value in probs.items():
        setattr(decider, key, value)
    fake_llm.push(draft(query=LOSS))
    # The question names time, so the decision model's grain counts.
    r = ask("Where is the loss each month?", seeded)
    assert r.route == "model" and len(fake_llm.prompts) == 1


@pytest.mark.parametrize("question", ["What is the total fraud loss?", "What was the fraud loss in 2024?"])
def test_code_decides_no_grain_when_the_question_names_no_time(fake_llm, seeded, decider, question):
    # Monthly data makes the model unsure of the grain; a question with no time word has none.
    decider.grain = {"month": 0.54, "none": 0.44}
    r = ask(question, seeded)
    assert r.route == "decision" and fake_llm.prompts == []
    assert r.query.time is None or r.query.time.grain is None


@pytest.mark.parametrize("question", ["How has the fraud loss moved?", "Show the monthly fraud loss.",
                                      "What is the fraud loss trend?", "Fraud loss over time.",
                                      # the insight eval: "moving" and "changing" got no series
                                      "How is the fraud loss moving?", "How is fraud loss changing?",
                                      "How is fraud loss trending?"])
def test_a_time_word_leaves_the_grain_to_the_decision_model(fake_llm, seeded, decider, question):
    decider.grain = {"month": 0.54, "none": 0.44}
    fake_llm.push(draft(query=LOSS))
    assert ask(question, seeded).route == "model"


def test_a_metric_name_inside_a_longer_name_is_one_metric():
    from living_reports.harness.decision_route import named_metrics
    from living_reports.semantic.model import Term

    terms = [Term(id="attrition_rate", label="Attrition rate"),
             Term(id="voluntary_attrition_rate", label="Voluntary attrition rate"),
             Term(id="leavers", label="Leavers")]
    assert named_metrics("What is the voluntary attrition rate by location?", terms) == ["voluntary_attrition_rate"]
    assert named_metrics("What is the attrition rate?", terms) == ["attrition_rate"]
    assert named_metrics("Compare the attrition rate and the voluntary attrition rate.", terms) == [
        "attrition_rate", "voluntary_attrition_rate"]
    assert named_metrics("Show leavers and the attrition rate.", terms) == ["attrition_rate", "leavers"]


@pytest.mark.parametrize("question", [
    "Fraud loss by region, excluding Web.",
    "Which regions have a fraud rate above 3%?",
    "Show fraud loss by region and sales channel.",
    "Show fraud loss and fraud rate by region.",
    "List the orders with the largest fraud loss.",
])
def test_a_question_that_needs_more_than_a_choice_goes_to_the_language_model(fake_llm, seeded, decider, question):
    decider.breakdown = {"dealers.region": 0.95, "none": 0.05}
    fake_llm.push(draft(query=LOSS))
    r = ask(question, seeded)
    assert r.route == "model" and len(fake_llm.prompts) == 1


def test_model_mode_keeps_the_language_model_router(fake_llm, seeded, decider, monkeypatch):
    monkeypatch.setenv("LR_ROUTE_MODE", "model")
    fake_llm.push(draft(query=LOSS))
    r = ask("What is the total fraud loss?", seeded)
    assert r.route == "model" and len(fake_llm.prompts) == 1


def test_no_decision_model_means_the_language_model_routes(fake_llm, seeded, monkeypatch):
    monkeypatch.delenv("LR_DECIDE_MODEL", raising=False)
    fake_llm.push(draft(query=LOSS))
    assert ask("What is the total fraud loss?", seeded).route == "model"


def test_the_decision_route_shows_what_code_read(fake_llm, seeded, decider):
    decider.breakdown = {CHANNEL: 0.9, "none": 0.1}
    r = ask("Give me the top 3 channels by fraud loss.", seeded)
    texts = [a.text for a in r.assumptions]
    assert "Only as many items as the question asks for." in texts


def test_one_named_value_of_the_breakdown_is_a_filter_with_no_breakdown(fake_llm, seeded, decider):
    # The decision model read "the Telesales channel" as a breakdown by channel.
    decider.metric = {"fraud_rate": 0.9, "fraud_loss": 0.1}
    decider.breakdown = {CHANNEL: 0.85, "none": 0.15}
    r = ask("What is the fraud rate in the Telesales channel?", seeded)
    assert r.route == "decision" and r.query.dimensions == []
    assert [(f.member, f.op, f.values) for f in r.query.filters] == [(CHANNEL, "eq", ["Telesales"])]


@pytest.mark.parametrize("question, grain, kept", [
    ("How many orders were placed in January 2025?", "month", False),
    ("How many orders were placed in 2024?", "year", False),
    ("Show monthly orders in 2024.", "month", True),
])
def test_a_grain_as_long_as_the_named_period_is_dropped(fake_llm, seeded, decider, question, grain, kept):
    decider.metric = {"order_count": 0.95, "fraud_loss": 0.05}
    decider.grain = {grain: 0.9, "none": 0.1}
    r = ask(question, seeded)
    assert r.route == "decision" and r.query.time.start is not None
    assert (r.query.time.grain == grain) is kept


def test_a_question_that_names_no_metric_goes_to_the_language_model(fake_llm, seeded, decider):
    # A choice over the metrics always picks one, so "the fraud number" reads as fraud loss at 0.9.
    fake_llm.push(draft(query=LOSS))
    r = ask("What is the fraud number?", seeded)
    assert r.route == "model" and len(fake_llm.prompts) == 1


def test_a_plural_metric_name_names_the_metric():
    """The insight eval: "How are return rates moving by category?" got a clarify question."""
    from living_reports.harness.decision_route import mentions_metric, named_metrics
    from living_reports.semantic.model import Term

    terms = [Term(id="return_rate", label="Return rate"), Term(id="revenue", label="Revenue")]
    assert mentions_metric("How are return rates moving by category?", terms)
    assert named_metrics("How are return rates moving by category?", terms) == ["return_rate"]
    assert not mentions_metric("How are returns moving?", terms)


@pytest.mark.parametrize("question", ["How is fraud loss changing by sales channel?",
                                      "How is the fraud loss moving?"])
def test_a_movement_word_with_no_grain_goes_to_the_language_model(fake_llm, seeded, decider, question):
    """The insight eval: d3:4b chose no grain for "How is revenue changing by channel?", so the
    report had no series; the language model router chose a monthly series."""
    decider.grain = {"none": 0.9, "month": 0.1}
    fake_llm.push(draft(query=LOSS))
    assert ask(question, seeded).route == "model"


def test_a_period_word_with_no_grain_stays_with_the_decision_model(fake_llm, seeded, decider):
    decider.grain = {"none": 0.9, "month": 0.1}
    assert ask("What was the fraud loss last year?", seeded).route == "decision"
