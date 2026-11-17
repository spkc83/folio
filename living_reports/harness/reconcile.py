"""Checks that a decision model and the code entity finder add to the router.

Each rule adds a check or fills a gap. None replaces a choice the router made:
- ``fill``: a value, period, or count that the question names and the router left out.
  One rule changes a router choice: a change "after March 2025" keeps the months before.
- ``decision_questions`` and ``doubt_*``: when the decision model disagrees with the router
  about whether the question can be answered, the route carries a doubt. The ladder then
  asks the next rung; without one, the router's outcome stands.
- ``metric_options``: a vague question, or a confident other reading, becomes a clarify.
"""
from living_reports.config import Settings
from living_reports.engine import Query, TimeRange
from living_reports.engine.query import Filter, flat_filters
from living_reports.harness import decide, decision_route
from living_reports.harness.dates import exclusive_end
from living_reports.harness.entities import Found
from living_reports.semantic.model import Vocabulary

ANSWERABLE = (
    "Can one of the metrics in the state answer this question from past data? A forecast, a "
    "prediction, or a thing that no metric measures cannot be answered."
)
WHICH_METRIC = "Which metric does the question ask about?"
DOUBT_REFUSE = "The decision model doubts that the metrics can answer this question."
DOUBT_ANSWER = "The decision model reads this question as answerable."
CLARIFY = "Which measure do you mean?"


def decision_questions(question: str, vocabulary: Vocabulary) -> tuple[dict, dict]:
    """The state and the questions for one decision request, from the graph's terms."""
    metrics = {
        t.id: f"{t.label}: {t.definition or t.label}"
        + (f" Also called: {', '.join(t.synonyms)}." if t.synonyms else "")
        for t in vocabulary.metrics
    }
    state = {
        "question": question,
        "metrics": list(metrics.values()),
        "dimensions": [t.label for t in vocabulary.dimensions],
    }
    questions = {"answerable": decide.noul(ANSWERABLE)}
    if 2 <= len(metrics) <= decide.MAX_OPTIONS:
        questions["metric"] = decide.choice(WHICH_METRIC, metrics)
        questions |= decision_route.questions(vocabulary)
    return state, questions


def doubt_refusal(answers: dict[str, decide.Answer] | None, settings: Settings) -> str | None:
    """The router refused; a confident decision model reads the question as answerable."""
    a, m = (answers or {}).get("answerable"), (answers or {}).get("metric")
    if a is None or m is None or a.noul is None:
        return None
    confident = m.top(1) and m.top(1)[0][1] >= settings.decide_confident
    return DOUBT_ANSWER if a.noul >= settings.decide_confident and confident else None


def doubt_answer(answers: dict[str, decide.Answer] | None, settings: Settings) -> str | None:
    """The router answered; the decision model finds the question not answerable."""
    a = (answers or {}).get("answerable")
    if a is None or a.noul is None:
        return None
    return DOUBT_REFUSE if a.noul < settings.decide_answerable_floor else None


def _with_months_before(time: TimeRange | None, since) -> tuple[TimeRange, str | None]:
    """A series that starts at the start of the data, so that the months after ``since`` can be
    compared with the months before it. The router reads "after March 2025" as a start date."""
    if time is not None and time.grain is not None and time.start is None:
        return time, None  # the whole history already
    start = time.start if time else None
    if start is not None and start not in (since, _month_before(since)):
        return time, None  # a start that the question does not name: the router's choice
    grain = (time.grain if time else None) or "month"
    end = time.end if time else None
    return (TimeRange(field=time.field if time else None, end=end, grain=grain),
            f"The months before {since:%B %Y} too, to compare.")


def _month_before(day):
    return day.replace(year=day.year - (day.month == 1), month=(day.month - 2) % 12 + 1)


def fill(query: Query, found: Found, vocabulary: Vocabulary,
         question: str = "") -> tuple[Query, list[str]]:
    """Add what the question names and the router left out. Never change a router choice,
    except the start of a change "after" a date (``_with_months_before``)."""
    notes: list[str] = []
    filtered = {f.member for f in flat_filters(query.filters)}
    filters = list(query.filters)
    for member, values in found.values.items():
        if member in filtered:
            continue
        if member in query.dimensions and len(values) < 2:
            continue  # "by channel, App looks high" names a value but asks for all
        op = "eq" if len(values) == 1 else "in"
        filters.append(Filter(member=member, op=op, values=values))
        notes.append(f"Only the {vocabulary.label(member)} that the question names.")
    time = query.time
    period = found.period
    if period is not None:
        # A month's last day as the end means "through it": compare the exclusive end.
        start, end = (time.start, exclusive_end(time.end)) if time else (None, None)
        # Fill a missing bound only. A half bound must agree with the named period:
        # {"start": 2024-01-01} for "in 2024" is the router's start with no end.
        if (start, end) == (None, None) or (end is None and start == period.start) or (
            start is None and end == period.end
        ):
            time = TimeRange(
                field=time.field if time else None,
                start=period.start,
                end=period.end,
                grain=time.grain if time else None,
            )
            notes.append("Only the period that the question names.")
    if found.since is not None and decision_route.asks_movement(question):
        time, note = _with_months_before(time, found.since)
        notes += [note] if note else []
    dimensions = list(query.dimensions)
    order = list(query.order)
    for f in filters:  # top level only: a value inside an "or" is not the only value
        if isinstance(f, Filter) and f.member in dimensions and f.op == "eq" and len(f.values) == 1:
            # A breakdown by a member that is filtered to one value is one row with the
            # same value. Keep the filter; show the one number.
            dimensions.remove(f.member)
            order = [(key, d) for key, d in order if key != f.member]
            notes.append(f"One {vocabulary.label(f.member)}, so no breakdown.")
    limit = query.limit
    if found.limit is not None and limit is None:
        limit = found.limit
        notes.append("Only as many items as the question asks for.")
    update = {"filters": filters, "time": time, "limit": limit, "dimensions": dimensions, "order": order}
    return query.model_copy(update=update), notes


def metric_options(
    query: Query, answers: dict[str, decide.Answer] | None, settings: Settings, named: bool = True
) -> list[Query]:
    """The router's query and one other reading, or only the router's query.

    Vague: the two most likely metrics are close and the router chose one of them.
    Disagreement: the decision model is confident of a metric the router did not choose.
    Unnamed: the question names no metric, so the router's metric is a guess; the other reading
    is the decision model's most likely other metric."""
    m = (answers or {}).get("metric")
    if m is None or len(query.metrics) != 1:
        return [query]
    ranked = m.top(2)
    if not ranked:
        return [query]
    chosen = query.metrics[0]
    best, best_p = ranked[0]
    other: str | None = None
    if best != chosen and best_p >= settings.decide_confident:
        other = best
    elif not named and len(ranked) == 2:
        other = best if best != chosen else ranked[1][0]
    elif len(ranked) == 2 and best_p < settings.decide_unsure:
        pair = [ranked[0][0], ranked[1][0]]
        if chosen in pair and ranked[0][1] + ranked[1][1] >= settings.decide_confident:
            other = pair[1] if pair[0] == chosen else pair[0]
    if other is None:
        return [query]
    swapped = query.model_copy(
        update={
            "metrics": [other],
            "order": [(other if key == chosen else key, d) for key, d in query.order],
            "having": [
                h.model_copy(update={"metric": other}) if h.metric == chosen else h
                for h in query.having
            ],
        }
    )
    return [query, swapped]


def option_label(query: Query, vocabulary: Vocabulary) -> str:
    label = vocabulary.label(query.metrics[0])
    if query.dimensions:
        label += " by " + " and ".join(vocabulary.label(d) for d in query.dimensions)
    return label
