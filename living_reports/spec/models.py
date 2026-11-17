"""The report specification. This is the only output format of the LLM."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Objective = Literal["why", "status", "decide", "trust"]
View = Literal["narrative", "dashboard", "brief", "evidence"]

VIEW_FOR_OBJECTIVE: dict[str, View] = {
    "why": "narrative",
    "status": "dashboard",
    "decide": "brief",
    "trust": "evidence",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Prose(_Strict):
    type: Literal["prose"] = "prose"
    text: str


class Tile(_Strict):
    type: Literal["tile"] = "tile"
    fact_id: str
    label: str
    delta: Literal["previous_period"] | None = None
    delta_label: str | None = None
    spark_ref: str | None = None


class Bars(_Strict):
    type: Literal["bars"] = "bars"
    query_ref: str
    title: str


class Line(_Strict):
    type: Literal["line"] = "line"
    query_ref: str
    title: str


class Table(_Strict):
    type: Literal["table"] = "table"
    query_ref: str
    title: str


class SmallMultiples(_Strict):
    type: Literal["small_multiples"] = "small_multiples"
    query_ref: str
    title: str


class DeltaBars(_Strict):
    """``query_ref`` is the current period by member, ``against_ref`` the previous one."""

    type: Literal["delta_bars"] = "delta_bars"
    query_ref: str
    against_ref: str
    title: str


class Segmented(_Strict):
    type: Literal["segmented"] = "segmented"
    query_ref: str
    title: str


class Action(_Strict):
    """A recommendation. ``basis`` holds the ids of the claims it follows from."""

    type: Literal["action"] = "action"
    text: str
    basis: list[str] = []


Block = Annotated[
    Prose | Tile | Bars | Line | Table | SmallMultiples | DeltaBars | Segmented | Action,
    Field(discriminator="type"),
]


class Assumption(_Strict):
    id: str
    text: str


class FollowUp(_Strict):
    """A follow-up chip under a section: a governed query that adds a section on a click."""

    label: str
    query: dict[str, Any]


class Review(_Strict):
    """How a section's own words were checked. Code proves each cited claim against the data;
    the words between the claims and what the text leaves out get a weaker check.

    ``wording``: "connectors" (code: only connecting words such as "also"), "model" (a
    decision model reviewed them: probable, not proven), "code" (code wrote fixed sentences),
    or "none" (no review). ``omissions``: "model" when a decision model checked that no
    important claim is left out, else "none". ``outdated``: a refresh changed a value that
    the model's review read."""

    wording: Literal["connectors", "model", "code", "none"]
    omissions: Literal["model", "none"]
    model: str | None = None
    outdated: bool = False


class Section(_Strict):
    id: str
    heading: str
    intent: str
    blocks: list[Block]
    decisions: list[str] = []
    # "code" when the model's text did not pass the checks and code wrote the section.
    narration: Literal["model", "code"] = "model"
    # Cited claims that are not true for the data of the last refresh.
    stale_claims: list[str] = []
    # Follow-up chips, most useful first: code makes them, a decision model orders them.
    follow_ups: list[FollowUp] = []
    # Members that the writer proposed: code gives them the claims of a named member.
    focus: list[str] = []
    # How the words between the claims were checked. None: written before this was recorded.
    review: Review | None = None


ClaimKind = Literal["value", "compare", "rank", "share", "extreme", "change", "trend", "target", "peak"]


class EvidenceRow(BaseModel):
    label: str
    fact: str
    highlight: bool = False


class SeriesPoint(BaseModel):
    label: str
    fact: str
    # The data does not fill the period, such as Jul–Nov 2025 in a half-year series.
    partial: bool = False


class Evidence(BaseModel):
    """What the evidence drawer shows for a claim."""

    counted: str = ""
    by: str | None = None
    # The comparison table: the claimed segment and its neighbors.
    rows: list[EvidenceRow] = []
    # The values over time, for a change or a trend.
    series: list[SeriesPoint] | None = None
    definition: str | None = None


class Claim(BaseModel):
    """A typed statement that code computes, checks, and renders.

    ``text`` may hold ``{fact:id}`` tokens. ``params`` holds the words that are not values
    (labels, period names), so that code can derive the text again from the facts.

    ``word`` is the claim's finding word, such as "highest", "below", or "rose sharply". A
    writer's sentence cites it as ``{word:id}``. ``about`` holds the member labels that the
    claim is about, such as ["North"]: a sentence with the word must name them.
    """

    id: str
    kind: ClaimKind
    text: str
    facts: list[str]
    query_ref: str
    segment: dict[str, str] = {}
    # The inclusive display period, such as ("2024-01-01", "2025-11-30").
    period: tuple[str, str] | None = None
    evidence: Evidence = Evidence()
    params: dict[str, Any] = {}
    word: str | None = None
    about: list[str] = []


class ReportSpec(_Strict):
    title: str
    kicker: str = ""
    objective: Objective
    assumptions: list[Assumption] = []
    sections: list[Section]
    # Every candidate claim of every section, by id. Text cites them as {claim:id}.
    claims: dict[str, Claim] = {}
