"""Validate a report specification against its facts and queries.

A number in text must be a ``{fact:<id>}`` token. This keeps every value traceable, because
the model cannot write a value of its own. The system writes ``kicker``, so dates can stay there.

A finding must come from a claim: a ``{claim:<id>}`` token (code writes the claim's whole
sentence) or a ``{word:<id>}`` token (code writes the claim's finding word, such as "highest",
inside the writer's own sentence). The word guard rejects trend, rank, comparison, and cause
words that the model writes outside tokens.
"""
import re
from collections.abc import Collection

from pydantic import BaseModel

from living_reports.metrics.runner import Fact, Result, ref_of
from living_reports.spec.models import (
    Action,
    Bars,
    Claim,
    DeltaBars,
    Line,
    Prose,
    ReportSpec,
    Segmented,
    SmallMultiples,
    Table,
    Tile,
)

TOKEN = re.compile(r"\{fact:([\w.\-+]+)\}")
CLAIM = re.compile(r"\{claim:([\w.\-+]+)\}")
WORD = re.compile(r"\{word:([\w.\-+]+)\}")
ANY_CLAIM = re.compile(r"\{(claim|word):([\w.\-+]+)\}")
# Words that state a trend, a rank, or a comparison. Only a claim may state these in prose.
FINDING_WORDS = (
    r"increas\w*|decreas\w*|grow\w*|grew|declin\w*|ris(?:e|es|en|ing)|rose|fell"
    r"|fall(?:s|en|ing)?|drop\w*|jump\w*|surg\w*|spik\w*|consistent\w*|steadil\w*"
    r"|highest|lowest|largest|smallest|the\s+(?:most|least|top|bottom)|more\s+than|less\s+than"
    r"|higher\s+than|lower\s+than|fewer|greater|exceed\w*|outpac\w*"
    # A judgment from the metric's direction or target. Only a claim may state it.
    r"|worse|worst|worsen\w*|better|best|improv\w*"
    r"|(?:miss\w*|meet\w*|met|beat\w*|above|below|over|under|on|off)\s+(?:the\s+|its\s+)?target"
)
# Words that state a cause. No model text may state these, a recommendation included.
CAUSE_WORDS = r"because|due\s+to|driven\s+by|caused|led\s+to|result\s+of"
CLAIM_WORDS = re.compile(rf"\b(?:{FINDING_WORDS}|{CAUSE_WORDS})\b", re.IGNORECASE)
_CAUSE = re.compile(rf"\b(?:{CAUSE_WORDS})\b", re.IGNORECASE)
_MARKUP = re.compile(r"<[a-zA-Z/!?]")
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
# A date names a period. It is a label, not a measured value, so it does not need a trace.
_DATES = re.compile(
    rf"\b(?:19|20)\d\d-\d\d(?:-\d\d)?\b"
    rf"|\b(?:(?:{_MONTHS})\.?|Q[1-4]|H[12]|in|of|for|during|from|to|since|until|by|and|year)\s+(?:19|20)\d\d\b(?!-\d)"
    rf"|\b(?:19|20)\d\d(?=\s+(?:to|and|through)\b|[.,;:]|$)",
    re.IGNORECASE,
)
_NUMBER_WORDS = re.compile(
    r"\b(hundred|thousand|million|billion|trillion|percent|dozen|twenty|thirty|forty|fifty"
    r"|sixty|seventy|eighty|ninety|double[ds]?|doubling|triple[ds]?|tripling|twice|quadrupled"
    r"|halved)\b",
    re.IGNORECASE,
)


_NUMBER_CHAR = re.compile(r"[\w.,]")


def tokenize_values(text: str, facts: dict[str, Fact]) -> str:
    """Turn a copied value into its fact token, only where the match is exact and unique.

    Small models copy values instead of writing tokens. A value that matches one traced fact
    is the same claim, so it can carry the trace. A value shared by two facts stays raw, and
    validation then rejects it: a wrong trace is worse than no answer.
    """
    owners: dict[str, set[str]] = {}
    for fact in facts.values():
        if fact.state == "ok" and re.search(r"\d", fact.formatted):
            owners.setdefault(fact.formatted, set()).add(fact.id)
    for value in sorted(owners, key=len, reverse=True):
        if len(owners[value]) != 1:
            continue
        (fact_id,) = owners[value]
        parts, start = [], 0
        for match in re.finditer(re.escape(value), text):
            before = text[match.start() - 1] if match.start() else ""
            after = text[match.end():match.end() + 2]
            # A comma or a point continues a number only when a digit follows it.
            continues = after[:1].isdigit() or (after[:1] in ",." and after[1:2].isdigit())
            touching = _NUMBER_CHAR.match(before) or continues
            if touching or before == "{":
                continue
            parts.append(text[start:match.start()] + f"{{fact:{fact_id}}}")
            start = match.end()
        text = "".join(parts) + text[start:]
    return text


class SpecIssue(BaseModel):
    code: str
    message: str
    path: str


_AVERAGE = re.compile(r"\b(average|avg|mean|median|typical)\b", re.I)
_TOTAL = re.compile(r"\b(total|sum)\b", re.I)
_NOUN = {"sum": "a total", "count": "a count", "count_distinct": "a count",
         "avg": "an average", "ratio": "a rate"}
_CLAUSE_END = re.compile(r"[.;:!?,]")
_WINDOW_WORDS = 6


def aggregates(queries: dict[str, Result]) -> dict[str, str]:
    """The aggregate of each metric in the queries. A result cached before it was recorded has none."""
    return {
        metric: meta["agg"]
        for result in queries.values()
        for metric, meta in result.metric_meta.items()
        if meta.get("agg")
    }


def check_aggregates(
    text: str, path: str, facts: dict[str, Fact], aggs: dict[str, str]
) -> list[SpecIssue]:
    """A word just before a value must not contradict how the value was computed.

    Example: "an average order value of {fact:q1_total.order_value}" when order value is a sum.
    The window is the words of the same clause before the token, at most six of them.
    """
    issues = []
    for match in TOKEN.finditer(text):
        fact = facts.get(match.group(1))
        if fact is None or fact.binding is None or fact.binding.share:
            continue
        agg = aggs.get(fact.binding.metric)
        if agg not in _NOUN:
            continue
        before = _CLAUSE_END.split(TOKEN.sub(" ", text[: match.start()]))[-1]
        window = " ".join(before.split()[-_WINDOW_WORDS:])
        wrong = _AVERAGE if agg in ("sum", "count", "count_distinct") else _TOTAL
        found = wrong.search(window)
        if found:
            other = "an average" if wrong is _AVERAGE else "a total"
            issues.append(SpecIssue(
                code="wrong_aggregate",
                message=f'{match.group(0)} is {_NOUN[agg]}, not {other}. Remove "{found.group(1)}".',
                path=path,
            ))
    return issues


def _outside(text: str) -> str:
    """The text that the writer wrote: the text outside fact, claim, and word tokens."""
    return ANY_CLAIM.sub(" ", TOKEN.sub(" ", text))


def check_text(
    text: str, path: str, facts: dict[str, Fact], claims: dict[str, Claim] | None = None
) -> list[SpecIssue]:
    issues = []
    for fact_id in TOKEN.findall(text):
        if fact_id not in facts:
            issues.append(SpecIssue(code="unknown_fact", message=f"No fact {fact_id}", path=path))
    for _, claim_id in ANY_CLAIM.findall(text):
        if claim_id not in (claims or {}):
            issues.append(SpecIssue(code="unknown_claim", message=f"No claim {claim_id}", path=path))
    bare = _outside(text)
    undated = _DATES.sub("", bare)
    if re.search(r"\d", undated) or _NUMBER_WORDS.search(undated):
        issues.append(SpecIssue(code="raw_number", message="Use a fact token", path=path))
    if _MARKUP.search(bare):
        issues.append(SpecIssue(code="markup", message="Markup is not allowed", path=path))
    return issues


def check_words(text: str, path: str, action: bool = False) -> list[SpecIssue]:
    """The word guard: a trend, rank, comparison, or cause word outside the tokens.

    A recommendation (``action``) needs verbs such as "increase" or "raise", so it is checked
    for cause words only."""
    words_re = _CAUSE if action else CLAIM_WORDS
    found = sorted({m.group(0).lower() for m in words_re.finditer(_outside(text))})
    if not found:
        return []
    words = ", ".join(f'"{w}"' for w in found)
    return [SpecIssue(code="claim_word", path=path,
                      message=f"{words} states a finding. Write the claim's word token, such as "
                              "{word:<claim id>}, or cite the claim as {claim:<claim id>}.")]


_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# A word that reverses the meaning of the next words: "not the highest".
_NEGATION = re.compile(r"(?:\b(?:not|never|no|nor|hardly|barely)\b|n't)(?:\W+\w+){0,2}\W*$", re.IGNORECASE)
# The values that a claim is about; its target value is context, not its subject.
_OWN_VALUES = ("fact", "first", "last", "pct")
MIN_LABEL = 3  # a shorter label ("No") is a common word too


def own_values(claim: Claim) -> list[str]:
    """The fact ids that a claim is about: its value, the ends of its change, or the points of
    its trend. A sentence with the claim's word token must hold one of them."""
    own = [claim.params[k] for k in _OWN_VALUES if isinstance(claim.params.get(k), str)]
    return own + [f for f in claim.params.get("points", []) if isinstance(f, str)]


def _names(label: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text, re.IGNORECASE) is not None


def check_word_tokens(text: str, path: str, claims: dict[str, Claim]) -> list[SpecIssue]:
    """The checks of each ``{word:id}`` token in the writer's own sentence.

    The sentence must hold one of the claim's own values as a fact token, name the claim's
    member, name no other member of the report, and not negate the word. Example: "West had
    the {word:q1.extreme.max.fraud_loss} fraud loss at {fact:q1.fraud_loss.South}" shows
    South's value under West's name, so it is rejected."""
    issues = []
    known = {a for c in claims.values() for a in c.about if len(a) >= MIN_LABEL}
    for sentence in _SENTENCE.split(text):
        words = [m for m in WORD.finditer(sentence) if m.group(1) in claims]
        if not words:
            continue
        cited = [claims[c] for _, c in ANY_CLAIM.findall(sentence) if c in claims]
        allowed = {a for c in cited for a in c.about}
        plain = _outside(sentence)
        values = set(TOKEN.findall(sentence))
        others = sorted(a for a in known - allowed if _names(a, plain))
        if others:
            issues.append(SpecIssue(code="word_wrong_member", path=path, message=(
                f"The sentence names {', '.join(others)}, but its word token is about "
                f"{', '.join(allowed) or 'the total'}.")))
        for m in words:
            claim, token = claims[m.group(1)], m.group(0)
            if claim.word is None:
                issues.append(SpecIssue(code="no_word", path=path,
                                        message=f"{token} has no word. Cite {{claim:{claim.id}}}."))
                continue
            own = own_values(claim)
            if not values & set(own):
                need = " or ".join(f"{{fact:{f}}}" for f in own)
                issues.append(SpecIssue(code="word_without_value", path=path,
                                        message=f"A sentence with {token} must hold {need}."))
            missing = [a for a in claim.about if not _names(a, plain)]
            if missing and not others:
                issues.append(SpecIssue(code="word_wrong_member", path=path,
                                        message=f"A sentence with {token} must name {', '.join(missing)}."))
            if _NEGATION.search(sentence[:m.start()]):
                issues.append(SpecIssue(code="negated_word", path=path,
                                        message=f"Do not negate {token}. Choose the claim that is true."))
    return issues


def _plain(text: str, path: str, facts, claims, guarded: bool) -> list[SpecIssue]:
    """A title or heading: plain words, no token, and (when guarded) no claim word."""
    issues = check_text(text, path, facts, claims)
    if TOKEN.search(text) or ANY_CLAIM.search(text):
        issues.append(SpecIssue(code="token_in_title", message="Use plain words", path=path))
    return issues + (check_words(text, path) if guarded else [])


def validate_spec(
    spec: ReportSpec,
    facts: dict[str, Fact],
    queries: dict[str, Result],
    guard: Collection[str] = (),
) -> list[SpecIssue]:
    """The issues of ``spec``. ``guard`` holds the section ids (and ``"title"``) that new
    model text wrote: the word guard and the action basis rule apply to these only, so text
    written before claims existed still validates."""
    aggs = aggregates(queries)
    claims = spec.claims
    issues = _plain(spec.title, "title", facts, claims, "title" in guard)
    for assumption in spec.assumptions:
        issues += check_text(assumption.text, f"assumptions.{assumption.id}", facts, claims)
    for section in spec.sections:
        path = f"sections.{section.id}"
        guarded = section.id in guard
        issues += _plain(section.heading, f"{path}.heading", facts, claims, guarded)
        for i, block in enumerate(section.blocks):
            where = f"{path}.blocks.{i}"
            if isinstance(block, Prose | Action):
                issues += check_text(block.text, where, facts, claims)
                issues += _claim_facts(block.text, where, facts, claims)
                issues += check_aggregates(block.text, where, facts, aggs)
                if guarded:
                    issues += check_words(block.text, where, action=isinstance(block, Action))
                    issues += check_word_tokens(block.text, where, claims)
                if isinstance(block, Action):
                    issues += _basis(block, where, claims, guarded)
            elif isinstance(block, Tile):
                fact = facts.get(block.fact_id)
                if fact is None:
                    issues.append(
                        SpecIssue(code="unknown_fact", message=block.fact_id, path=where)
                    )
                elif fact.state != "ok":
                    issues.append(SpecIssue(code="empty_fact", message=fact.label, path=where))
                issues += _tile_extras(block, queries, where)
            elif isinstance(block, Bars | Line | Table | SmallMultiples | DeltaBars | Segmented):
                issues += _chart_issues(block, queries, where)
    return issues


def _claim_facts(text: str, where: str, facts, claims) -> list[SpecIssue]:
    """Each fact that a cited claim's text shows must exist, or its chip has no value."""
    return [
        SpecIssue(code="unknown_fact", message=f"No fact {f} (claim {c})", path=where)
        for _, c in ANY_CLAIM.findall(text)
        if c in claims
        for f in TOKEN.findall(claims[c].text)
        if f not in facts
    ]


def _basis(block: Action, where: str, claims: dict[str, Claim], guarded: bool) -> list[SpecIssue]:
    issues = [
        SpecIssue(code="unknown_claim", message=f"No claim {c}", path=where)
        for c in block.basis
        if c not in claims
    ]
    if guarded and not block.basis:
        issues.append(SpecIssue(code="action_without_basis", path=where,
                                message="An action needs action_basis: the claims it follows from."))
    return issues


def _tile_extras(block: Tile, queries: dict[str, Result], where: str) -> list[SpecIssue]:
    issues = []
    if block.spark_ref is not None and block.spark_ref not in queries:
        issues.append(SpecIssue(code="unknown_query", message=block.spark_ref, path=where))
    if block.delta is not None:
        prev = f"{ref_of(block.fact_id)}_prev"
        cur = f"{ref_of(block.fact_id)}_cur"
        if prev not in queries or cur not in queries or not block.delta_label:
            issues.append(SpecIssue(code="no_previous_period", message=prev, path=where))
    return issues


def _chart_issues(block, queries: dict[str, Result], where: str) -> list[SpecIssue]:
    refs = [block.query_ref, *([block.against_ref] if isinstance(block, DeltaBars) else [])]
    issues = [
        SpecIssue(code="unknown_query", message=ref, path=where)
        for ref in refs
        if ref not in queries
    ]
    if isinstance(block, Segmented) and block.query_ref in queries:
        meta = queries[block.query_ref].metric_meta
        first = next(iter(meta.values()), {})
        if first.get("format") == "percent":
            # Parts of a rate do not add up to a whole, so a part-to-whole bar would mislead.
            issues.append(SpecIssue(code="segmented_rate", message=block.query_ref, path=where))
    return issues
