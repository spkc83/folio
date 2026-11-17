"""The certified question set: each published section's question and its exact query.

Publishing certifies. The publisher is the verifier, and the publish time is the
verification time. A certified question answers at zero model cost while the definitions
that its query uses are unchanged. Unrelated graph writes (a new report, a thread, another
metric's change) do not change that.
"""
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from living_reports.engine import OutOfScope, Query, QueryRejected
from living_reports.engine.compile import compile
from living_reports.engine.query import totals_of, upgrade_definition
from living_reports.reports.editions import Edition, EditionStore
from living_reports.reports.payload import Verified
from living_reports.semantic.model import SemanticModel
from living_reports.store import Store

log = logging.getLogger(__name__)

STALE = "This certified question no longer matches the model."
_COSMETIC = {"label", "synonyms", "version"}
_warned: set[str] = set()
# A query that no longer validates or compiles names something the model does not have.
_NO_LONGER_RUNS = (KeyError, ValidationError, OutOfScope, QueryRejected)
_COLUMNS = (
    "id, question, query, report_id, section_id, graph_version, definitions, expected, "
    "verified_by, verified_at, edition, content_hash"
)


class CertifiedQuestion(BaseModel):
    id: str
    question: str
    query: dict[str, Any]
    report_id: str
    # The certification binds to this published edition and its content, not to the
    # live report, which can change after publish.
    edition: int
    content_hash: str
    section_id: str
    graph_version: int
    definitions: str
    expected: dict[str, str]
    verified_by: str
    verified_at: datetime
    current: bool = True


def question_id(question: str, query: dict) -> str:
    normalized = re.sub(r"\s+", " ", question).strip().lower()
    body = normalized + "|" + json.dumps(query, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def definitions_hash(query: dict, model: SemanticModel) -> str:
    """A hash of every definition the query reads: its metrics (and the metrics a ratio
    reads), the members it groups, filters, or measures by, the datasets of those members
    with their time fields, and each relationship that the compiled query joins through.
    Raises one of ``_NO_LONGER_RUNS`` when the query names something the model does not
    have."""
    plan = compile(Query.model_validate(upgrade_definition(query, model)), model).plan
    metric_ids = set(plan.metric_versions)
    members = set(plan.member_versions)
    datasets = {*plan.base_datasets, *(member.split(".", 1)[0] for member in members)}
    for name in datasets:
        if time_field := model.dataset(name).time_field:
            members.add(f"{name}.{time_field}")
    joined = {name for names in plan.joins.values() for name in names}
    relationships = {r.name: r for r in model.relationships if r.name in joined}
    # Names for people (label, synonyms) and the node version do not change a value.
    # The definition text stays in: it states what the number means.
    docs = {
        "metrics": {
            i: model.metric(i).model_dump(mode="json", exclude=_COSMETIC) for i in sorted(metric_ids)
        },
        "members": {
            i: model.field(i).model_dump(mode="json", exclude=_COSMETIC) for i in sorted(members)
        },
        "datasets": {
            n: model.dataset(n).model_dump(mode="json", exclude={"fields", "version"})
            for n in sorted(datasets)
        },
        "relationships": {
            n: relationships[n].model_dump(mode="json", exclude={"version"})
            for n in sorted(relationships)
        },
    }
    return hashlib.sha256(json.dumps(docs, sort_keys=True).encode()).hexdigest()


def _is_current(query: dict, definitions: str, model: SemanticModel) -> bool:
    try:
        return definitions_hash(query, model) == definitions
    except _NO_LONGER_RUNS:
        return False


def _prefix(ref: str, query: Query) -> str:
    """The fact prefix of a section's totals: ``q1_total`` for a breakdown, else ``q1``."""
    return f"{ref}_total" if query.dimensions or query.is_time_series else ref


def _expected_ids(ref: str, query: dict, model: SemanticModel) -> list[str]:
    parsed = Query.model_validate(upgrade_definition(query, model))
    return [f"{_prefix(ref, parsed)}.{metric}" for metric in parsed.metrics]


def certify_edition(edition: Edition, user: str, store: Store) -> list[CertifiedQuestion]:
    """One row per section that has a query. Exploratory editions write nothing."""
    if edition.tier == "exploratory":
        return []
    # The definitions as the edition saw them, not as they are at a later read.
    model = store.graph.model(valid_at=edition.known_at, known_at=edition.known_at)
    now = store.now()
    rows = []
    for section in edition.spec.sections:
        ref = "q" + section.id[1:]
        query = edition.query_defs.get(ref)
        if query is None:
            continue
        expected = {
            i: edition.facts[i].formatted
            for i in _expected_ids(ref, query, model)
            if i in edition.facts
        }
        rows.append(
            CertifiedQuestion(
                id=question_id(section.intent, query),
                question=section.intent,
                query=query,
                report_id=edition.report_id,
                edition=edition.number,
                content_hash=edition.content_hash,
                section_id=section.id,
                graph_version=edition.graph_version,
                definitions=definitions_hash(query, model),
                expected=expected,
                verified_by=user,
                verified_at=now,
            )
        )
    for cq in rows:
        # A new certification answers the earlier "certified answer changed" cards.
        store.execute(
            "UPDATE changes SET status = 'done', payload = json_set(payload, '$.status', 'done') "
            "WHERE status = 'open' AND json_extract(payload, '$.subject.certified_id') = ?",
            [cq.id],
        )
        store.execute(
            f"INSERT OR REPLACE INTO certified_questions ({_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                cq.id,
                cq.question,
                json.dumps(cq.query),
                cq.report_id,
                cq.section_id,
                cq.graph_version,
                cq.definitions,
                json.dumps(cq.expected),
                cq.verified_by,
                store.ts(cq.verified_at),
                cq.edition,
                cq.content_hash,
            ],
        )
    return rows


def _row(row: tuple, model: SemanticModel) -> CertifiedQuestion:
    # Rows written before the query engine hold the old query form; read them in the new one.
    query = upgrade_definition(json.loads(row[2]), model)
    return CertifiedQuestion(
        id=row[0],
        question=row[1],
        query=query,
        report_id=row[3],
        section_id=row[4],
        graph_version=row[5],
        definitions=row[6],
        expected=json.loads(row[7]),
        verified_by=row[8],
        verified_at=row[9],
        edition=row[10],
        content_hash=row[11],
        current=_is_current(query, row[6], model),
    )


def _valid(cq: CertifiedQuestion, model: SemanticModel) -> bool:
    """A row whose query no longer validates, or names a retired metric or member, is
    skipped. The warning is logged once for each row."""
    try:
        definitions_hash(cq.query, model)
    except _NO_LONGER_RUNS:
        if cq.id not in _warned:
            _warned.add(cq.id)
            log.warning("Certified question %s no longer matches the model", cq.id)
        return False
    return True


# Each certification is one row per (question, report, edition): a published edition's
# certification is history. The question set is the newest row for each question.
_NEWEST = (
    f"SELECT {_COLUMNS} FROM (SELECT *, row_number() OVER ("
    "PARTITION BY id ORDER BY verified_at DESC, report_id, edition DESC) AS n "
    "FROM certified_questions) WHERE n = 1"
)


def all_certified(store: Store) -> list[CertifiedQuestion]:
    """The newest certification of each question whose query still runs, newest first."""
    rows = store.rows(f"{_NEWEST} ORDER BY verified_at DESC, id")
    model = store.graph.model()
    return [cq for cq in (_row(r, model) for r in rows) if _valid(cq, model)]


def list_certified(store: Store, limit: int = 8) -> list[CertifiedQuestion]:
    return all_certified(store)[:limit]


def get_certified(store: Store, certified_id: str) -> CertifiedQuestion | None:
    row = store.row(f"{_NEWEST} AND id = ?", [certified_id])
    return _row(row, store.graph.model()) if row else None


def _certified_edition(store: Store, cq: CertifiedQuestion) -> Edition | None:
    """The edition that was certified, only while it still has the certified content."""
    from living_reports.reports.store import ReportStore

    edition = EditionStore(store, ReportStore(store)).get(cq.report_id, cq.edition)
    if edition is None or edition.status != "published" or edition.content_hash != cq.content_hash:
        return None
    return edition


def _verified(store: Store, rows: list[tuple]) -> Verified | None:
    """One badge for one edition's rows: current only while every section is current."""
    if not rows:
        return None
    model = store.graph.model()
    cqs = [_row(r, model) for r in rows]
    if _certified_edition(store, cqs[0]) is None:
        return None
    return Verified(
        by=cqs[0].verified_by, at=cqs[0].verified_at, current=all(cq.current for cq in cqs)
    )


def verified_for_edition(store: Store, report_id: str, number: int) -> Verified | None:
    """The badge for one edition: only an edition that was published and certified."""
    rows = store.rows(
        f"SELECT {_COLUMNS} FROM certified_questions WHERE report_id = ? AND edition = ? "
        "ORDER BY verified_at DESC, id",
        [report_id, number],
    )
    return _verified(store, rows)


def verified_for(store: Store, report_id: str) -> Verified | None:
    """The badge for the live report: only while its content equals the newest certified
    edition. A refresh, a follow-up section, or a tier change after publish removes it."""
    from living_reports.reports.store import ReportStore

    rows = store.rows(
        f"SELECT {_COLUMNS} FROM certified_questions WHERE report_id = ? AND edition = "
        "(SELECT max(edition) FROM certified_questions WHERE report_id = ?) "
        "ORDER BY verified_at DESC, id",
        [report_id, report_id],
    )
    reports = ReportStore(store)
    report = reports.get(report_id)
    if not rows or report is None:
        return None
    if EditionStore(store, reports).draft_hash(report) != rows[0][11]:
        return None
    return _verified(store, rows)


def answer_certified(cq: CertifiedQuestion, ctx):
    """Open the certified edition at zero cost while it is current; else answer again."""
    from living_reports.harness.builder import AskResult, _cost, answer

    try:
        definitions_hash(cq.query, ctx.store.graph.model())
    except _NO_LONGER_RUNS:
        # The query no longer validates, or it names a retired metric or member.
        return AskResult(kind="failed", message=STALE, cost=_cost(ctx))
    edition = _certified_edition(ctx.store, cq)
    if cq.current and edition is not None:
        # The published edition, never the live draft: the draft can change after publish.
        ctx.rung = "certified"
        return AskResult(
            kind="report",
            report_id=edition.report_id,
            view=edition.view,
            edition=edition.number,
            cost=_cost(ctx),
        )
    # The answer is traced but not verified: the definitions moved or the edition is gone.
    ctx.rung = "small_spec"
    report = ctx.reports.get(cq.report_id)
    objective = report.spec.objective if report is not None else "why"
    return answer(cq.question, None, ctx, choice=cq.query, objective=objective)


def check_certified(ctx) -> list:
    """Run each certified question's totals on the active source. A moved value is a
    change item of kind ``question``. One query per question, no model call."""
    from living_reports.metrics.runner import run_query, to_facts
    from living_reports.reports.changes import ChangeItem
    from living_reports.sources.registry import source_for

    store = ctx.store
    model = store.graph.model()
    items = []
    data_time = source_for(ctx.source_id).data_time().isoformat()
    for cq in all_certified(store):
        try:
            definitions = definitions_hash(cq.query, model)
        except _NO_LONGER_RUNS:
            continue  # a retired metric or member: the question cannot run
        query = Query.model_validate(cq.query)
        ref = "q" + cq.section_id[1:]
        prefix = _prefix(ref, query)
        now = {f.id: f for f in to_facts(run_query(totals_of(query), ctx.source_id, store), prefix)}
        moved = [
            f"{now[i].label}: {before} → {now[i].formatted}"
            for i, before in cq.expected.items()
            if i in now and now[i].formatted != before
        ]
        if not moved:
            continue
        items.append(
            ChangeItem(
                # Stable across unrelated graph writes; new for new data or a new definition.
                id=f"question:{cq.id}:{ctx.source_id}@{data_time}:{definitions[:12]}",
                kind="question",
                title=f"Certified answer changed: {cq.question}",
                detail=f"{'; '.join(moved)}. Verified by {cq.verified_by} "
                f"on {cq.verified_at.date()}.",
                affected_reports=[cq.report_id],
                subject={"certified_id": cq.id},
            )
        )
    return items
