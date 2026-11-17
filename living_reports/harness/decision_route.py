"""Decision-first routing: the decision model picks the metric, the breakdown, and the grain;
code adds what the question names (values, a period, a count).

Every part of a query is a choice from the semantic model, so a decision model can route a
plain question without a language model. A question that needs more than these choices, or a
decision below the confidence floor, goes to the language-model router."""
import re

from living_reports.config import Settings
from living_reports.engine import Query, TimeRange
from living_reports.semantic.model import Term, Vocabulary

from . import decide
from .entities import Found, Period

NONE = "none"
NO_BREAKDOWN = "No breakdown: one number for the whole, or for the values that the question names."
NO_GRAIN = "Not over time: one number for the whole period."

# Questions that need an operator, a threshold, two metrics, or raw rows: the language model.
_UNSUPPORTED = re.compile(
    r"\b(?:excluding|except|other than|but not|without|not in|above|below|more than|less than|"
    r"at least|at most|over \d|under \d|per|ratio|list|which orders|each order|customers who)\b",
    re.IGNORECASE,
)
_MONTHS = {"month": 1, "quarter": 3, "year": 12}  # the months in one period of each grain


def _months(period: Period) -> int:
    return (period.end.year - period.start.year) * 12 + period.end.month - period.start.month


_LOWEST = re.compile(r"\b(?:bottom|lowest|smallest|least)\b", re.IGNORECASE)


def questions(vocabulary: Vocabulary) -> dict[str, dict]:
    """The breakdown and the grain questions. Empty when the options do not fit a choice."""
    dims = [t for t in vocabulary.dimensions if not t.is_time]
    if len(dims) + 1 > decide.MAX_OPTIONS:
        return {}
    breakdown = {NONE: NO_BREAKDOWN} | {
        t.id: f"By {t.label}: one row for each {t.label.lower()}." for t in dims
    }
    grain = {NONE: NO_GRAIN} | {g: f"Over time, one value for each {g}." for g in vocabulary.grains}
    return {
        "breakdown": decide.choice("Which breakdown does the question ask for?", breakdown),
        "grain": decide.choice("Does the question ask for values over time, and by which period?", grain),
    }


def _words(term: Term, last_word: bool) -> list[str]:
    words = [term.label, *term.synonyms]
    if last_word and " " in term.label:
        words.append(term.label.rsplit(" ", 1)[1])
    return [w.lower() for w in words if w]


def _names(question: str, term: Term, last_word: bool = False) -> bool:
    lowered = question.lower()
    # "return rates" names "Return rate": a plural is the same name.
    return any(re.search(rf"\b{re.escape(w)}s?\b", lowered) for w in _words(term, last_word))


# A question with none of these words asks for one number, not a series: code sets no grain.
_TIME_WORDS = re.compile(
    r"\b(?:day|daily|week|weekly|month|monthly|quarter|quarterly|year|yearly|annual|annually|"
    r"trend|trends|trending|over time|series|history|moved|move|moves|moving|change|changed|changes|"
    r"changing|grow|grows|grew|growing|growth|rise|rises|rising|fall|falls|fell|falling|since|"
    r"seasonal|seasonality)\b",
    re.IGNORECASE,
)


# Words that ask how a value moves. With one of them, "no grain" is a misread: the language
# model routes instead (the insight eval: "How is revenue changing by channel?" got no series).
_MOVEMENT = re.compile(
    r"\b(?:trend|trends|trending|over time|moved|move|moves|moving|change|changed|changes|changing|"
    r"grow|grows|grew|growing|growth|rise|rises|rising|fall|falls|fell|falling)\b",
    re.IGNORECASE,
)


def asks_movement(question: str) -> bool:
    """The question asks how a value moves: "change", "moving", "grew", "trend"."""
    return bool(_MOVEMENT.search(question))


def _spans(question: str, term: Term) -> list[tuple[int, int]]:
    lowered = question.lower()
    return [m.span() for w in _words(term, False) for m in re.finditer(rf"\b{re.escape(w)}s?\b", lowered)]


def named_metrics(question: str, metrics: list[Term]) -> list[str]:
    """The metrics the question names. A name inside a longer metric name ("attrition rate" in
    "voluntary attrition rate") does not count on its own."""
    spans = {t.id: _spans(question, t) for t in metrics}
    named = []
    for term in metrics:
        others = [o for other, found in spans.items() if other != term.id for o in found]
        alone = [s for s in spans[term.id]
                 if not any(o[0] <= s[0] and s[1] <= o[1] and o != s for o in others)]
        if alone:
            named.append(term.id)
    return named


def mentions_metric(question: str, metrics: list[Term]) -> bool:
    """The question names some metric, by its name, a synonym, or the last word of its name
    ("loss" for "Fraud loss"). A question that names none ("the fraud number") is vague."""
    return any(_names(question, t, last_word=True) for t in metrics)


def _top(answers: dict[str, decide.Answer], key: str) -> list[tuple[str, float]]:
    answer = answers.get(key)
    return answer.top(2) if answer is not None else []


def decided_query(question: str, answers: dict[str, decide.Answer] | None, vocabulary: Vocabulary,
                  found: Found, settings: Settings) -> Query | None:
    """The query that the decision model reads, or ``None`` when the language model must route."""
    if not answers or _UNSUPPORTED.search(question):
        return None
    floor = settings.decide_confident
    answerable = answers.get("answerable")
    if answerable is None or answerable.noul is None or answerable.noul < floor:
        return None
    metric = _top(answers, "metric")
    if not metric:
        return None
    vague = (len(metric) == 2 and metric[0][1] < settings.decide_unsure
             and metric[0][1] + metric[1][1] >= floor)
    if metric[0][1] < floor and not vague:
        return None  # a vague pair becomes the existing clarify between the two metrics
    breakdown, grain = _top(answers, "breakdown"), _top(answers, "grain")
    if not _TIME_WORDS.search(question):
        grain = [(NONE, 1.0)]  # no time word: one number, whatever the data's grain
    elif grain and grain[0][0] == NONE and _MOVEMENT.search(question):
        return None  # "How is revenue changing?" asks for a series: the language model routes
    if not breakdown or not grain or breakdown[0][1] < floor or grain[0][1] < floor:
        return None
    named = named_metrics(question, vocabulary.metrics)
    if len(named) > 1:
        return None  # two metrics
    if not named and not vague:
        return None  # a choice always picks a metric: one the question does not name is a guess
    asked = [t for t in vocabulary.dimensions
             if not t.is_time and t.id not in found.values and _names(question, t, last_word=True)]
    if len(asked) > 1:
        return None  # two breakdowns
    m, d, g = metric[0][0], breakdown[0][0], grain[0][0]
    if len(found.values.get(d, [])) == 1:
        d = NONE  # "the Telesales channel" names one value: a filter, not a breakdown
    if found.period is not None and g in _MONTHS and _months(found.period) <= _MONTHS[g]:
        g = NONE  # "in January 2025" by month is one row: show the one number
    order = [(m, "asc")] if found.limit is not None and _LOWEST.search(question) else []
    return Query(
        metrics=[m],
        dimensions=[] if d == NONE else [d],
        time=None if g == NONE else TimeRange(grain=g),
        order=order,
    )
