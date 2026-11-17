"""The narrative judge: a decision model checks what code cannot.

It runs after the deterministic checks (fact and claim tokens, the word guard) and before a
report is saved. It never edits text: a failed item goes back to the writer as a repair note.
``rubric`` judges the whole section text against its data brief. ``unsupported`` (one
question per sentence) is the older judge.
"""
import re

from living_reports.config import get_settings
from living_reports.harness import decide
from living_reports.metrics.runner import Fact
from living_reports.spec.validate import TOKEN

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CHECK = (
    "Is this text true to the values in the state? Each value must be described as what it is "
    "(a total is not an average, a rate is not a count), each comparison must match the values, "
    "and the text must not state a cause or a prediction that the values cannot show.\n"
    "Text: {text}"
)


def sentences(texts: list[str]) -> list[str]:
    return [s for t in texts for s in _SENTENCE_END.split(t.strip()) if s]


def _readable(text: str, facts: dict[str, Fact], labels: dict[str, str]) -> str:
    def value(m: re.Match) -> str:
        f = facts.get(m.group(1))
        return f"{f.formatted} [{labels.get(f.id, f.label)}]" if f else "unknown"

    return TOKEN.sub(value, text)


def unsupported(
    texts: list[str],
    facts: dict[str, Fact],
    labels: dict[str, str],
    question: str,
    turn_id: str,
) -> list[str] | None:
    """The sentences that the judge rejects, or ``None`` when no judge is available.

    ``labels`` gives each fact id its label with the kind of value, such as
    ``Order value (total)``. A sentence past the 64th is not judged."""
    items = sentences(texts)[: decide.MAX_QUESTIONS]
    if not items:
        return []
    state = {
        "question": question,
        "values": [
            f"{f.formatted} = {labels.get(i, f.label)}"
            + (f": {f.definition}" if f.definition else "")
            for i, f in facts.items()
        ],
    }
    questions = {
        f"s{n}": decide.noul(_CHECK.format(text=_readable(s, facts, labels)))
        for n, s in enumerate(items, start=1)
    }
    answers = decide.ask(state, questions, "judge", turn_id)
    if answers is None:
        return None
    floor = get_settings().judge_floor
    return [
        s
        for n, s in enumerate(items, start=1)
        if (a := answers.get(f"s{n}")) is not None and a.noul is not None and a.noul < floor
    ]


# Each rubric item: the question, the passing answer, and the repair note when it fails.
RUBRIC = {
    "r1": ("free_text is the writer's own words. Code wrote and checked each [cited claim], "
           "[cited word], and [value] in it. Do the writer's own words add a fact, a comparison, "
           "a trend, or a cause that these do not state, such as \"slightly\", \"strong growth\", "
           "or \"because of\"?", False,
           "The writer's own words, outside the cited claims, add a finding that no claim states. "
           "State findings only with word tokens, value tokens, and claim tokens."),
    "r2": ("Does the text answer the question?", True,
           "The text does not answer the question. Cite the claims that answer it."),
    "r3": ("Is the most important claim in the brief (the first one in the list) left out of the text?",
           False, "The text leaves out the most important claim (the first one in the list)."),
    "r4": ("Does the recommendation follow from the claims it cites?", True,
           "The recommendation does not follow from the claims it cites. Cite the claims it "
           "follows from, or write no action."),
}


# Words that connect claims and cannot state a fact on their own. When the writer's own words
# are only these, code passes R1 without a model call.
CONNECTORS = frozenset(
    "in short also and then next finally first second third overall meanwhile by contrast "
    "however still so here is are the a an to sum up put simply for context as well on other "
    "hand at same time moreover further furthermore additionally addition summary note that "
    "this these which with of".split()
)


def only_connectors(free_text: str) -> bool:
    words = re.sub(r"\[(?:cited claim|cited word|value)\]", " ", free_text.lower())
    return all(w in CONNECTORS for w in re.findall(r"[a-z]+", words))


def rubric(brief: str, text: str, question: str, has_action: bool, turn_id: str,
           free_text: str | None = None) -> list[str] | None:
    """The repair notes of the failed rubric items, or ``None`` when no judge is available.

    ``brief`` is the data brief as the writer read it. ``text`` is the rendered output: each
    claim written out in [square brackets], each value written out."""
    items = {k: v for k, v in RUBRIC.items() if has_action or k != "r4"}
    if free_text is not None and only_connectors(free_text):
        items.pop("r1")  # connecting words alone cannot state a fact
    state = {"question": question, "brief": brief, "text": text}
    if free_text is not None:
        state["free_text"] = free_text
    answers = decide.ask(state, {k: decide.noul(q) for k, (q, _, _) in items.items()}, "judge", turn_id)
    if answers is None:
        return None
    floor = get_settings().judge_floor
    notes = []
    for key, (_, want_yes, note) in items.items():
        answer = answers.get(key)
        if answer is None or answer.noul is None:
            continue
        failed = answer.noul < floor if want_yes else answer.noul > 1 - floor
        if failed:
            notes.append(note)
    return notes
