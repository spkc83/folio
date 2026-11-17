"""Section-level operations on a saved report.

Each operation changes one section and saves through ``save_report``. Deterministic changes
(a chip edit, a chart swap, a removal, a prose edit) make no model call. A wording command
makes one structured call, and its words pass the same checks as narration.
"""
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from living_reports.api.auth import role_of
from living_reports.engine import OutOfScope, Query
from living_reports.engine.query import stored_query
from living_reports.harness.brief import prompt_text
from living_reports.harness.builder import (
    MAX_BARS,
    WORD_RULES,
    AskContext,
    _matches_rows,
    _no_rows_message,
    _problems,
    prepare_section,
    required_claims,
    rubric_problems,
    run_section,
    save_report,
    section_blocks,
    ste_problems,
    token_hint,
    tokenize_claims,
    with_review,
    write_checked,
)
from living_reports.harness.claims import cited_claims, keep_stale_facts, outdated_reviews, recheck
from living_reports.harness.llm import TurnCost, escape_data
from living_reports.harness.selection import Selection, with_selection  # noqa: F401 (re-exported)
from living_reports.metrics.runner import Result
from living_reports.reports.store import StoredReport, Thread
from living_reports.semantic.kinds import ThreadPayload
from living_reports.spec.models import (
    Action,
    Bars,
    DeltaBars,
    Line,
    Prose,
    Section,
    Segmented,
    SmallMultiples,
    Table,
    Tile,
)
from living_reports.spec.validate import (
    TOKEN,
    SpecIssue,
    check_text,
    check_words,
    tokenize_values,
    validate_spec,
)
from living_reports.store import Store

CHART_FORMS = ("bars", "table", "line", "small_multiples", "delta_bars", "segmented")
_BLOCKS = {
    "bars": Bars,
    "table": Table,
    "line": Line,
    "small_multiples": SmallMultiples,
    "segmented": Segmented,
}
MULTIPLES = range(2, 7)
LAST_SECTION = "A report needs at least one section."


class CommandResult(BaseModel):
    kind: Literal["ok", "failed"]
    message: str | None = None
    wording_dropped: bool = False
    # Kept words were written for the earlier query; their values changed under them.
    wording_stale: bool = False
    cost: TurnCost = TurnCost()


def close_threads(ctx: AskContext, report: StoredReport, gone: Callable[[Thread], bool]) -> None:
    """Close each open thread whose section or number no longer exists in the report."""
    for thread in ctx.reports.threads_for(report.id):
        if thread.status != "open" or not gone(thread):
            continue
        thread.status = "closed"
        payload = ThreadPayload(
            report_id=report.id,
            section_id=thread.section_id,
            fact_id=thread.fact_id,
            status="closed",
            decision=None,
        )
        with ctx.store.transaction():
            ctx.store.graph.put(f"thread:{thread.id}", "Thread", payload, by=ctx.user)
            ctx.reports.save_thread(thread)


def ref_for(section_id: str) -> str:
    """The query ref of a section: ``s2`` -> ``q2``."""
    return "q" + section_id[1:]


def _owns(key: str, ref: str) -> bool:
    """True for a fact id or query key of ``ref``: ``q1``, ``q1.x``, ``q1_total.x``, ``q1_cur``."""
    return key == ref or key.startswith((f"{ref}.", f"{ref}_"))


def section_index(report: StoredReport, section_id: str) -> int:
    for i, section in enumerate(report.spec.sections):
        if section.id == section_id:
            return i
    raise KeyError(section_id)


def _with_section(report: StoredReport, index: int, section: Section, **update) -> StoredReport:
    sections = [*report.spec.sections]
    sections[index] = section
    spec = report.spec.model_copy(update={"sections": sections})
    return report.model_copy(update={"spec": spec, **update})


def _joined(report: StoredReport, user: str) -> list[str]:
    return sorted({*report.contributors, user})


def rerun_section(
    report: StoredReport, section_id: str, query: Query, ctx: AskContext
) -> tuple[StoredReport, bool, bool]:
    """Run a section again on an edited query, with no model call.

    Words whose tokens still resolve stay; the rest are dropped (the second value). Kept words
    were written for the earlier query, so they can be stale when the query changed (the third).
    """
    ref = ref_for(section_id)
    index = section_index(report, section_id)
    if ref not in report.query_defs:
        raise KeyError(ref)
    model = ctx.store.graph.model()
    if not _matches_rows(query, model, ctx):
        raise OutOfScope(_no_rows_message(query, model))
    results, new_facts = run_section(query, ref, ctx)
    section = report.spec.sections[index]
    data = prepare_section(section.intent, query, ref, results, new_facts, model, ctx, report,
                           focus=section.focus)
    facts = {k: f for k, f in report.facts.items() if not _owns(k, ref)}
    facts |= data.claims.facts(new_facts)
    queries = {k: r for k, r in report.queries.items() if not _owns(k, ref)} | results
    words = [b for b in section.blocks if isinstance(b, Prose | Action)]
    kept = [b for b in words if all(t in facts for t in TOKEN.findall(b.text))]
    blocks = [
        *(b for b in kept if isinstance(b, Prose)),
        *section_blocks(query, ref, results, new_facts),
        *(b for b in kept if isinstance(b, Action)),
    ]
    definition = query.model_dump(mode="json", exclude_none=True)
    changed_query = definition != report.query_defs[ref]
    defs = {**report.query_defs, ref: definition}
    changed = _with_section(
        report,
        index,
        section.model_copy(update={"blocks": blocks}),
        facts=facts,
        queries=queries,
        query_defs=defs,
        contributors=_joined(report, ctx.user),
    )
    changed.spec = recheck(changed.spec, data.claims.claims, {ref}, facts)
    changed.spec = outdated_reviews(changed.spec, report.facts, facts)
    changed.facts = keep_stale_facts(changed.spec, facts, report.facts)
    saved = save_report(changed, ctx)
    close_threads(ctx, saved, lambda t: t.fact_id is not None and t.fact_id not in facts)
    stale = changed_query and any(isinstance(b, Prose) for b in kept)
    return saved, len(kept) < len(words), stale


def can_edit(user: str, report: StoredReport) -> bool:
    """Authors only: the author role or the report's author (open question 3).

    Rights never come from ``contributors``: a chip edit makes a viewer a contributor, and
    that must not make the viewer an editor of the words.
    """
    return role_of(user) == "author" or user == report.author


def _members(result: Result, column: str) -> int:
    index = result.columns.index(column)
    return len({row[index] for row in result.rows})


def chart_forms(query: Query, results: dict[str, Result], ref: str) -> list[str]:
    """The chart forms that fit the query, in ``CHART_FORMS`` order."""
    if ref not in results:
        return []
    main = results[ref]
    series = query.is_time_series
    one = len(query.dimensions) == 1 and not series
    bounded = query.time is not None and query.time.start is not None and query.time.end is not None
    first = next(iter(main.metric_meta.values()), {})
    fits = {
        "bars": one and main.row_count <= MAX_BARS,
        "table": bool(query.dimensions) or series,
        "line": series,
        "small_multiples": series
        and len(query.dimensions) == 1
        and _members(main, query.dimensions[0]) in MULTIPLES,
        # The main result is the current period only when the query has both bounds.
        "delta_bars": one and bounded and f"{ref}_prev_members" in results,
        "segmented": one and first.get("format") != "percent",
    }
    return [form for form in CHART_FORMS if fits[form]]


def _chart_index(blocks: list) -> int | None:
    return next(
        (i for i, b in enumerate(blocks) if not isinstance(b, Tile | Prose | Action)), None
    )


def swap_chart(report: StoredReport, section_id: str, form: str, ctx: AskContext) -> StoredReport:
    """Show the section's data in another chart form. No query and no model call."""
    ref = ref_for(section_id)
    index = section_index(report, section_id)
    if ref not in report.query_defs:
        raise ValueError("This section has no query to chart.")
    query = stored_query(report.query_defs[ref], ctx.store.graph.model())
    allowed = chart_forms(query, report.queries, ref)
    if form not in allowed:
        raise ValueError(f"{form} does not fit this query; choose one of: {', '.join(allowed)}")
    section = report.spec.sections[index]
    blocks = [*section.blocks]
    at = _chart_index(blocks)
    title = blocks[at].title if at is not None else ""
    if form == "delta_bars":
        block = DeltaBars(query_ref=ref, against_ref=f"{ref}_prev_members", title=title)
    else:
        block = _BLOCKS[form](query_ref=ref, title=title)
    if at is None:
        actions = [i for i, b in enumerate(blocks) if isinstance(b, Action)]
        blocks.insert(actions[0] if actions else len(blocks), block)
    else:
        blocks[at] = block
    # A chart swap changes no fact and no word, so the user does not become a contributor.
    changed = _with_section(report, index, section.model_copy(update={"blocks": blocks}))
    return save_report(changed, ctx)


Rewrite = Literal["shorter", "expand", "regenerate"]


class _Rewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraphs: list[str]


_TASKS = {
    "shorter": "Rewrite these paragraphs in fewer words. Keep the most important claim tokens.",
    "expand": "Write one new paragraph that cites a claim the current paragraphs do not cite. "
    "Reply with that paragraph only.",
    "regenerate": "Write the paragraphs again from the claims.",
}

_REWRITE = """You rewrite the words of one section of a business report. Reply with JSON only:
{{"paragraphs": [str]}}

""" + WORD_RULES + """

Task: {task}

<data>
Section: {heading}
{brief}
Current paragraphs:
{paragraphs}
</data>
{repair}"""


def _with_prose(section: Section, command: Rewrite, paragraphs: list[str]) -> Section:
    new = [Prose(text=p) for p in paragraphs]
    blocks = [*section.blocks]
    spots = [i for i, b in enumerate(blocks) if isinstance(b, Prose)]
    if command == "expand":
        at = spots[-1] + 1 if spots else 0
        return section.model_copy(update={"blocks": [*blocks[:at], *new, *blocks[at:]]})
    at = spots[0] if spots else 0
    kept = [b for b in blocks if not isinstance(b, Prose)]
    return section.model_copy(update={"blocks": [*kept[:at], *new, *kept[at:]]})


def rewrite_section(
    report: StoredReport, section_id: str, command: Rewrite, ctx: AskContext
) -> StoredReport | None:
    """The writer loop on one section, with its data brief. ``None`` when every draft fails:
    nothing is saved. A section with no query has no claims, so it cannot be rewritten."""
    index = section_index(report, section_id)
    section = report.spec.sections[index]
    ref = ref_for(section_id)
    if ref not in report.query_defs:
        return None
    model = ctx.store.graph.model()
    query = stored_query(report.query_defs[ref], model)
    results = {k: r for k, r in report.queries.items() if _owns(k, ref)}
    base = [f for k, f in report.facts.items() if _owns(k, ref) and ".derived." not in k]
    data = prepare_section(section.intent, query, ref, results, base, model, ctx, report,
                           focus=section.focus)
    facts = report.facts | data.claims.facts(base)
    claims = report.spec.claims | {
        k: c for k, c in data.by_id.items() if k not in report.spec.claims
        or report.spec.claims[k].text == c.text
    }
    brief = escape_data(prompt_text(data.brief))
    old = [b.text for b in section.blocks if isinstance(b, Prose)]
    example = data.claims.claims[0].id if data.claims.claims else "id"

    def prompt_for(repair: str) -> str:
        return _REWRITE.format(
            example=example,
            required=required_claims(data),
            task=_TASKS[command],
            heading=escape_data(section.heading),
            brief=brief,
            paragraphs=escape_data("\n\n".join(old) or "None"),
            repair=repair,
        )

    def check(words: _Rewrite):
        paragraphs = [
            tokenize_values(tokenize_claims(p.strip(), claims, facts), facts)
            for p in words.paragraphs
            if p.strip()
        ]
        problems = _rewrite_problems(command, paragraphs, old) + ste_problems(paragraphs)
        candidate = _with_section(report, index, _with_prose(section, command, paragraphs), facts=facts)
        spec = candidate.spec.model_copy(update={"claims": claims})
        candidate.spec = recheck(spec, data.claims.claims, {ref}, facts)
        candidate.facts = keep_stale_facts(candidate.spec, facts, report.facts)
        issues = validate_spec(candidate.spec, facts, report.queries)
        issues += [i for n, p in enumerate(paragraphs) for i in check_words(p, f"paragraphs.{n}")]
        problems += _problems(issues)
        if issues and (hint := token_hint(paragraphs, data.by_id, facts)):
            problems.append(hint)
        if not problems:
            texts = [b.text for b in candidate.spec.sections[index].blocks if isinstance(b, Prose)]
            problems, review = rubric_problems(brief, texts, None, claims, facts, section.intent,
                                               ctx.turn_id)
            candidate.spec = with_review(candidate.spec, section.id, review)
        return problems, candidate

    written = write_checked(prompt_for, _Rewrite, ctx, check)
    if written is None:
        return None
    _, candidate = written
    candidate.contributors = _joined(report, ctx.user)
    return save_report(candidate, ctx)


def _rewrite_problems(command: Rewrite, paragraphs: list[str], old: list[str]) -> list[str]:
    if not paragraphs:
        return ["no paragraphs"]
    if command == "expand" and len(paragraphs) != 1:
        return ["write exactly one paragraph"]
    if command == "shorter" and sum(map(len, paragraphs)) >= sum(map(len, old)):
        return ["the text is not shorter; it must be shorter than before, not longer"]
    return []


def _cited_facts(claims: dict, ids: set[str]) -> set[str]:
    return {f for c in ids if c in claims for f in TOKEN.findall(claims[c].text)}


def remove_section(report: StoredReport, section_id: str, ctx: AskContext) -> StoredReport:
    """Remove a section with its query, results, and facts. Other ids do not change."""
    index = section_index(report, section_id)
    if len(report.spec.sections) == 1:
        raise ValueError(LAST_SECTION)
    ref = ref_for(section_id)
    sections = [s for i, s in enumerate(report.spec.sections) if i != index]
    # A claim of the removed query stays while another section's text or basis cites it.
    still = {c for s in sections for c in cited_claims(s)}
    claims = {k: c for k, c in report.spec.claims.items() if c.query_ref != ref or k in still}
    changed = report.model_copy(
        update={
            "spec": report.spec.model_copy(update={"sections": sections, "claims": claims}),
            "query_defs": {k: d for k, d in report.query_defs.items() if k != ref},
            "queries": {k: r for k, r in report.queries.items() if not _owns(k, ref)},
            "facts": {k: f for k, f in report.facts.items()
                      if not _owns(k, ref) or k in _cited_facts(claims, still)},
            "contributors": _joined(report, ctx.user),
        }
    )
    saved = save_report(changed, ctx)
    close_threads(ctx, saved, lambda t: t.section_id == section_id)
    return saved


Signal = Literal["up", "down"]


def record_feedback(store: Store, report_id: str, section_id: str, user: str, signal: Signal) -> None:
    """One signal per user and section. It does not change the report."""
    store.execute(
        "INSERT OR REPLACE INTO feedback VALUES (?, ?, ?, ?, ?)",
        [report_id, section_id, user, signal, store.ts(store.now())],
    )


def feedback_of(store: Store, report_id: str, section_id: str, user: str) -> Signal | None:
    row = store.row(
        "SELECT signal FROM feedback WHERE report_id = ? AND section_id = ? AND user = ?",
        [report_id, section_id, user],
    )
    return row[0] if row else None


EMPTY = "A section needs at least one paragraph."


class SpecRejected(Exception):
    """A person's words failed the text checks. Nothing was saved."""

    def __init__(self, issues: list[SpecIssue]):
        super().__init__("; ".join(f"{i.code}: {i.message}" for i in issues))
        self.issues = issues


def edit_prose(
    report: StoredReport, section_id: str, paragraphs: list[str], ctx: AskContext, user: str
) -> StoredReport:
    """Replace the section's paragraphs with a person's words. Tokens stay tokens.

    The text check runs on save. The full checker runs on the next edition.
    """
    index = section_index(report, section_id)
    section = report.spec.sections[index]
    path = f"sections.{section_id}"
    texts = [p.strip() for p in paragraphs if p.strip()]
    if not texts:
        raise SpecRejected([SpecIssue(code="empty", message=EMPTY, path=path)])
    issues = [
        i
        for n, t in enumerate(texts)
        for i in check_text(t, f"{path}.blocks.{n}", report.facts, report.spec.claims)
    ]
    if issues:
        raise SpecRejected(issues)
    blocks = [*section.blocks]
    spots = [i for i, b in enumerate(blocks) if isinstance(b, Prose)]
    at = spots[0] if spots else 0
    kept = [b for b in blocks if not isinstance(b, Prose)]
    new = [*kept[:at], *(Prose(text=t) for t in texts), *kept[at:]]
    changed = _with_section(
        report, index, section.model_copy(update={"blocks": new}),
        contributors=_joined(report, user),
    )
    return save_report(changed, ctx)


def reorder_blocks(
    report: StoredReport, section_id: str, order: list[int], ctx: AskContext
) -> StoredReport:
    """Put the section's blocks in a new order. ``order`` lists each old index once."""
    index = section_index(report, section_id)
    section = report.spec.sections[index]
    if sorted(order) != list(range(len(section.blocks))):
        raise ValueError("order must list every block once")
    blocks = [section.blocks[i] for i in order]
    changed = _with_section(report, index, section.model_copy(update={"blocks": blocks}))
    return save_report(changed, ctx)


def remove_block(report: StoredReport, section_id: str, at: int, ctx: AskContext) -> StoredReport:
    """Remove one tile or chart. A paragraph goes through the wording editor."""
    index = section_index(report, section_id)
    section = report.spec.sections[index]
    if not 0 <= at < len(section.blocks):
        raise ValueError(f"No block {at}")
    if isinstance(section.blocks[at], Prose):
        raise ValueError("Edit the words to remove a paragraph.")
    blocks = [b for i, b in enumerate(section.blocks) if i != at]
    changed = _with_section(report, index, section.model_copy(update={"blocks": blocks}))
    return save_report(changed, ctx)
