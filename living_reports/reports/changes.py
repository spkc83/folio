"""Find changes in data and definitions, and carry them into reports."""
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from folio_graph import Edge, GraphError, Node, NodeChange, Proposal
from living_reports.engine import OutOfScope, Query
from living_reports.engine.compile import compile
from living_reports.engine.query import stored_query
from living_reports.harness.builder import (
    AskContext,
    build_cube_into,
    prepare_section,
    run_section,
    section_blocks,
)
from living_reports.harness.claims import cross_facts, keep_stale_facts, outdated_reviews, recheck
from living_reports.harness.commands import ref_for
from living_reports.metrics.runner import Fact, run_query, to_facts
from living_reports.reports.editions import Edition
from living_reports.reports.lineage import OWN, proposers_of
from living_reports.reports.signoff import Forbidden, Signoff
from living_reports.reports.store import StoredReport
from living_reports.semantic.kinds import (
    IDENTIFIER,
    DimensionSpec,
    FieldPayload,
    FieldType,
    FilterSpec,
    Measure,
    MetricBindingPayload,
)
from living_reports.semantic.model import SemanticModel
from living_reports.sources import SourceError, normalize, rows_of
from living_reports.sources.build import build_demo_source, demo_moment
from living_reports.sources.registry import source_for
from living_reports.sources.types import field_type
from living_reports.spec.models import Action, Prose, Section
from living_reports.spec.validate import validate_spec

log = logging.getLogger(__name__)

Kind = Literal["new_data", "new_column", "new_value", "definition", "question"]
NEW_FOR = timedelta(hours=24)


class ChangeItem(BaseModel):
    id: str
    kind: Kind
    title: str
    detail: str
    affected_reports: list[str] = []
    status: Literal["open", "done"] = "open"
    proposal_id: str | None = None
    attention: list[str] = []
    detected_at: datetime | None = None
    # What changed, for suggestions: {"dimension", "value"}, {"column"}, {"metric"},
    # or {"certified_id"}.
    subject: dict[str, str] = {}
    # Computed when the feed is read, never stored.
    is_new: bool = False


def is_new(item: ChangeItem, now: datetime) -> bool:
    """New for 24 hours after detection. An item saved before detected_at existed is not new."""
    return item.detected_at is not None and now - item.detected_at < NEW_FOR


class FactValue(BaseModel):
    label: str
    value: Any
    formatted: str


class Impact(BaseModel):
    reports: list[str]
    before: FactValue
    after: FactValue


class FactChange(BaseModel):
    id: str
    label: str
    before: str
    after: str


class DefinitionChange(BaseModel):
    metric: str
    from_version: int
    to_version: int
    definition: str | None = None


class EditionDiff(BaseModel):
    changed_facts: list[FactChange]
    added_facts: list[FactChange]
    removed_facts: list[FactChange]
    added_sections: list[str]
    removed_sections: list[str]
    definition_changes: list[DefinitionChange]
    graph_versions: tuple[int, int]


def _save(item: ChangeItem, ctx: AskContext) -> None:
    """Insert stamps ``detected_at``; an update keeps the stored one."""
    store = ctx.store
    with store.transaction():
        stored = store.row(
            "SELECT json_extract(payload, '$.detected_at') FROM changes WHERE id = ?", [item.id]
        )
        if stored:
            item.detected_at = datetime.fromisoformat(stored[0]) if stored[0] else None
            store.execute(
                "UPDATE changes SET status = ?, payload = ? WHERE id = ?",
                [item.status, item.model_dump_json(exclude={"is_new"}), item.id],
            )
        else:
            if item.detected_at is None:
                item.detected_at = store.now()
            seq = store.row("SELECT coalesce(max(seq), 0) + 1 FROM changes")[0]
            store.execute(
                "INSERT INTO changes VALUES (?, ?, ?, ?)",
                [item.id, seq, item.status, item.model_dump_json(exclude={"is_new"})],
            )


def open_changes(ctx: AskContext, within_days: int = 7) -> list[ChangeItem]:
    """Open items, and done items detected in the last ``within_days`` days."""
    now = ctx.store.now()
    since = ctx.store.ts(now - timedelta(days=within_days))
    rows = ctx.store.rows(
        "SELECT payload FROM changes WHERE status = 'open' "
        # julianday: pydantic drops zero microseconds, so the ISO texts differ in length.
        "OR (status = 'done' AND julianday(json_extract(payload, '$.detected_at')) "
        ">= julianday(?)) ORDER BY seq",
        [since],
    )
    items = [ChangeItem.model_validate_json(r[0]) for r in rows]
    for item in items:
        item.is_new = is_new(item, now)
    return items


def _reports_using(node_id: str, ctx: AskContext) -> list[str]:
    ids = [d for d in ctx.store.graph.slice().dependents(node_id) if d.startswith("report:")]
    return sorted(i.removeprefix("report:") for i in ids)


def _timed(model: SemanticModel):
    """The first dataset with a time field: its rows are the ones that arrive."""
    return next((d for d in model.datasets if d.time_field), model.datasets[0])


def _probe_model(model: SemanticModel) -> SemanticModel:
    """The model plus two probe metrics: the first date of the timed dataset, and its rows."""
    timed = _timed(model)
    probes = {
        "first_seen": Measure(agg="min", field=f"{timed.name}.{timed.time_field}"),
        "row_count": Measure(agg="count", dataset=timed.name),
    }
    for metric_id, measure in probes.items():
        if measure.agg == "min" and timed.time_field is None:
            continue
        payload = MetricBindingPayload(
            name=metric_id, label=metric_id, format="number", measure=measure
        )
        model = model.with_metric(metric_id, payload)
    return model


def _profile(source_id: str, model: SemanticModel) -> dict[str, Any]:
    """What the source holds now: the columns of the model's tables, the rows of the timed
    dataset, and the first date of each value of each dimension."""
    source = source_for(source_id)
    catalog = source.introspect()
    probe = _probe_model(model)
    dimensions = [
        f.id for d in model.datasets for f in d.fields if f.dimension and not f.dimension.is_time
    ]
    types: dict[str, dict[str, str]] = {}
    for ds in model.datasets:
        try:
            info = catalog.table(ds.table)
        except KeyError:
            continue
        types[ds.name] = {c.name: c.source_type for c in info.columns}

    def run(query: Query) -> list:
        compiled = compile(query, probe, source.dialect)
        return rows_of(normalize(source.run(compiled.sql, compiled.params)))

    rows = run(Query(metrics=["row_count"]))[0][0]
    firsts = {}
    if "first_seen" in probe.metric_versions:
        for member in dimensions:
            found = run(Query(metrics=["first_seen"], dimensions=[member]))
            firsts[member] = {value: first for value, first in found}
    return {"types": types, "rows": rows, "firsts": firsts, "data_time": source.data_time()}


def _first_date(value: Any) -> str:
    return str(value.date() if hasattr(value, "date") else value)


def detect(before: dict[str, Any], after: dict[str, Any], ctx: AskContext) -> list[ChangeItem]:
    """Change items from two profiles of the same source, before and after new data."""
    model = ctx.store.graph.model()
    timed = _timed(model)
    # The item ids name the source and the new data time: the same change is one item.
    new = f"{ctx.source_id}@{after['data_time'].isoformat()}"
    items = []
    if after["rows"] > before["rows"]:
        items.append(
            ChangeItem(
                id=f"{new}:new_data",
                kind="new_data",
                title=f"{after['rows'] - before['rows']:,} new {timed.name} arrived",
                detail="Reports refresh on the new data, and each new edition waits for checks.",
                affected_reports=_reports_using(timed.name, ctx)
                or sorted(r.id for r in ctx.reports.list_reports() if r.query_defs),
            )
        )
    for dataset, types in after["types"].items():
        for column in sorted(types.keys() - before["types"].get(dataset, {}).keys()):
            if not IDENTIFIER.match(column):
                continue  # a name that is not a plain identifier cannot become a Field
            payload = FieldPayload(
                name=column,
                dataset=dataset,
                column=column,
                type=field_type(types[column]) or "string",
                dimension=DimensionSpec(),
            )
            proposal = ctx.store.graph.propose(
                NodeChange(
                    id=f"{dataset}.{column}", kind="Field", payload=payload.model_dump(mode="json")
                ),
                "system",
                "A new column arrived in the data.",
            )
            items.append(
                ChangeItem(
                    id=f"{new}:new_column:{dataset}.{column}",
                    kind="new_column",
                    title=f"New column: {column}",
                    detail="Approve it to make a new breakdown available.",
                    proposal_id=proposal.id,
                    subject={"column": column, "dataset": dataset},
                )
            )
    members = model.members
    for member, firsts in after["firsts"].items():
        known = before["firsts"].get(member, {})
        for value in sorted(set(firsts) - set(known), key=str):
            items.append(
                ChangeItem(
                    id=f"{new}:new_value:{member}:{value}",
                    kind="new_value",
                    title=f"New {members[member].label}: {value}",
                    detail=f"First seen on {_first_date(firsts[value])}.",
                    affected_reports=_reports_using(member, ctx),
                    subject={"dimension": member, "value": str(value)},
                )
            )
    return items


def _definition_item(ctx: AskContext) -> ChangeItem:
    """The demo definition change, for example "Fraud loss: exclude friendly fraud"."""
    from living_reports.demos import get_demo

    change = get_demo().definition
    live = ctx.store.graph.slice().node(change.metric)
    current = MetricBindingPayload.model_validate(live.payload)
    measure = current.measure.model_copy(update={"filter": change.filter})
    payload = current.model_copy(update={"measure": measure, "definition": change.definition})
    proposal = ctx.store.graph.propose(
        NodeChange(id=change.metric, kind="MetricBinding", payload=payload.model_dump(mode="json")),
        "system",
        change.reason,
    )
    return ChangeItem(
        id=f"definition:{proposal.change.id}:{proposal.id}",
        kind="definition",
        title=change.title,
        detail="This changes the meaning of the metric, so it needs an owner's approval.",
        affected_reports=_reports_using(change.metric, ctx),
        proposal_id=proposal.id,
        subject={"metric": proposal.change.id},
    )


def _rebuilt_spec(report: StoredReport, facts, results, model: SemanticModel):
    sections = []
    for section in report.spec.sections:
        ref = ref_for(section.id)
        definition = report.query_defs.get(ref)
        if definition is None:
            sections.append(section)
            continue
        keep = [b for b in section.blocks if isinstance(b, Prose)]
        end = [b for b in section.blocks if isinstance(b, Action)]
        query = stored_query(definition, model)
        built = section_blocks(query, ref, results, list(facts.values()))
        sections.append(Section(**{**section.model_dump(), "blocks": []}).model_copy(
            update={"blocks": [*keep, *built, *end]}
        ))
    return report.spec.model_copy(update={"sections": sections})


def refresh_report(report: StoredReport, ctx: AskContext) -> bool:
    """Rerun the report's queries. Narration stays, and its fact tokens take new values.

    Each cited claim is derived again from the new values. A claim that no longer holds is
    listed in its section's ``stale_claims``; its words are not changed."""
    facts: dict[str, Fact] = {}
    results = {}
    fresh = []
    model = ctx.store.graph.model()
    intents = {ref_for(s.id): s.intent for s in report.spec.sections}
    focus = {ref_for(s.id): s.focus for s in report.spec.sections}
    for ref, definition in report.query_defs.items():
        query = stored_query(definition, model)
        new_results, new_facts = run_section(query, ref, ctx)
        data = prepare_section(intents.get(ref, ""), query, ref, new_results, new_facts, model, ctx,
                               focus=focus.get(ref, ()))
        results |= new_results
        facts |= data.claims.facts(new_facts)
        fresh += data.claims.claims
    spec = _rebuilt_spec(report, facts, results, model)
    facts |= cross_facts(spec, facts)  # a comparison across sections reads two queries
    spec = recheck(spec, fresh, set(report.query_defs), facts)
    spec = outdated_reviews(spec, report.facts, facts)
    facts = keep_stale_facts(spec, facts, report.facts)
    if validate_spec(spec, facts, results):
        return False
    report.spec, report.facts, report.queries = spec, facts, results
    report.source_id = ctx.source_id
    build_cube_into(report, ctx.store)
    report.narration_refreshed = True
    ctx.reports.save(report)
    return True


def _refresh_and_check(report_ids: list[str], ctx: AskContext) -> list[str]:
    """Return the reports that could not refresh. A person must look at these."""
    signoff = Signoff(ctx.store, ctx.reports)
    failed = []
    for report_id in report_ids:
        report = ctx.reports.get(report_id)
        if not report or not report.query_defs:
            continue
        if refresh_report(report, ctx):
            signoff.freeze_and_check(report_id)
        else:
            failed.append(report_id)
    return failed


def advance_demo(ctx: AskContext) -> list[ChangeItem]:
    """Load the next demo moment: rebuild the demo source in place, then brief the changes.

    At the last moment this changes nothing and returns the open items.
    """
    if demo_moment(ctx.store) == 2:
        return open_changes(ctx)
    model = ctx.store.graph.model()
    before = _profile(ctx.source_id, model)
    # The source attaches the new file on its next run.
    build_demo_source(2)
    after = _profile(ctx.source_id, model)
    items = detect(before, after, ctx)
    items.append(_definition_item(ctx))
    for item in items:
        _save(item, ctx)
    refreshed = [r.id for r in ctx.reports.list_reports() if r.tier != "exploratory"]
    failed = _refresh_and_check(refreshed, ctx)
    if failed:
        for item in items:
            if item.kind == "new_data":
                item.attention = failed
                _save(item, ctx)
    _save_certified_changes(ctx)
    return open_changes(ctx)


def _save_certified_changes(ctx: AskContext) -> None:
    from living_reports.reports.certified import check_certified

    store = ctx.store
    for item in check_certified(ctx):
        stored = store.row("SELECT status FROM changes WHERE id = ?", [item.id])
        if stored and stored[0] == "done":
            continue  # resolved already, for example by a new publish
        with store.transaction():
            # One open card for each certified question: the newest values replace the rest.
            store.execute(
                "UPDATE changes SET status = 'done', payload = json_set(payload, '$.status', "
                "'done') WHERE status = 'open' AND id <> ? "
                "AND json_extract(payload, '$.subject.certified_id') = ?",
                [item.id, item.subject["certified_id"]],
            )
            _save(item, ctx)


class NoImpactPreview(Exception):
    """Only a change to a metric has a before-and-after value."""


def impact(proposal: Proposal, ctx: AskContext) -> Impact:
    change = proposal.change
    if not isinstance(change, NodeChange) or change.kind != "MetricBinding":
        raise NoImpactPreview("An impact preview is available for metric changes only.")
    query = Query(metrics=[change.id])
    try:
        before = to_facts(run_query(query, ctx.source_id, ctx.store), "before")[0]
        payload = MetricBindingPayload.model_validate(change.payload)
        preview = run_query(query, ctx.source_id, ctx.store, overrides={change.id: payload})
    except OutOfScope as error:
        raise NoImpactPreview(
            f"The metric {change.id} is new or cannot run yet, so it has no value to compare."
        ) from error
    except ValidationError as error:
        raise NoImpactPreview(f"This metric definition is not valid: {error}") from error
    after = to_facts(preview, "after")[0]
    return Impact(
        reports=_reports_using(change.id, ctx),
        before=FactValue(label=before.label, value=before.value, formatted=before.formatted),
        after=FactValue(label=after.label, value=after.value, formatted=after.formatted),
    )


def confirm_proposal(
    proposal: Proposal, by: str, store, context: Callable[[], AskContext]
) -> Node | Edge:
    """Confirm one proposal, then run the follow-up that a confirmed change needs.

    Every confirmation path (the graph route and the model page) goes through here. The
    change item of the proposal closes and the certified questions run again; a change to
    a live node also carries into the reports that use it. A new node has no reports yet.
    The caller checks the role and the proposer.

    The confirm is committed first. If the follow-up cannot get its source, it is skipped
    with a warning: the decision stands, and the next refresh catches up.
    """
    if by in proposers_of(store, proposal):
        raise Forbidden(OWN)
    change = proposal.change
    was_live = isinstance(change, NodeChange) and store.graph.slice().node(change.id) is not None
    with store.transaction():
        entity = store.graph.confirm(proposal.id, by)
    try:
        ctx = context()
    except SourceError as error:
        log.warning("The follow-up of proposal %s is skipped: %s", proposal.id, error)
        return entity
    apply_confirmed(store.graph.proposal(proposal.id) or proposal, ctx, refresh=was_live)
    return entity


def reject_proposal(proposal: Proposal, by: str, note: str, store) -> Proposal:
    """Reject one proposal. Every reject path goes through here; no proposer of the
    proposal's lineage may reject it. The caller checks the role."""
    if by in proposers_of(store, proposal):
        raise Forbidden(OWN)
    return store.graph.reject(proposal.id, by, note)


def apply_confirmed(proposal: Proposal, ctx: AskContext, refresh: bool = True) -> None:
    """Carry an approved change into the affected reports, then close its change item."""
    failed = _refresh_and_check(_reports_using(proposal.change.id, ctx), ctx) if refresh else []
    for item in open_changes(ctx):
        if item.proposal_id != proposal.id or item.status != "open":
            continue
        if failed:
            # The decision is made, but these reports still use the old definition.
            item.attention, item.proposal_id = failed, None
        else:
            item.status = "done"
        _save(item, ctx)
    _save_certified_changes(ctx)


def _change(fact_a: Fact | None, fact_b: Fact | None) -> FactChange:
    fact = fact_b or fact_a
    return FactChange(
        id=fact.id,
        label=fact.label,
        before=fact_a.formatted if fact_a else "",
        after=fact_b.formatted if fact_b else "",
    )


def diff_editions(a: Edition, b: Edition, ctx: AskContext) -> EditionDiff:
    changed = [
        _change(a.facts[i], b.facts[i])
        for i in a.facts.keys() & b.facts.keys()
        if a.facts[i].formatted != b.facts[i].formatted
    ]
    versions: dict[str, tuple[int, int]] = {}
    for i in a.facts.keys() & b.facts.keys():
        old, new = a.facts[i], b.facts[i]
        if old.metric and old.metric_version != new.metric_version:
            versions[old.metric] = (old.metric_version, new.metric_version)
    definitions = []
    terms = {t.id: t for t in ctx.store.graph.vocabulary().metrics} if versions else {}
    for metric, (old_version, new_version) in sorted(versions.items()):
        term = terms.get(metric)
        definitions.append(
            DefinitionChange(
                metric=metric,
                from_version=old_version,
                to_version=new_version,
                definition=term.definition if term else None,
            )
        )
    old_sections = {s.id: s.heading for s in a.spec.sections}
    new_sections = {s.id: s.heading for s in b.spec.sections}
    return EditionDiff(
        changed_facts=sorted(changed, key=lambda c: c.id),
        added_facts=sorted(
            (_change(None, b.facts[i]) for i in b.facts.keys() - a.facts.keys()),
            key=lambda c: c.id,
        ),
        removed_facts=sorted(
            (_change(a.facts[i], None) for i in a.facts.keys() - b.facts.keys()),
            key=lambda c: c.id,
        ),
        added_sections=[h for i, h in new_sections.items() if i not in old_sections],
        removed_sections=[h for i, h in old_sections.items() if i not in new_sections],
        definition_changes=definitions,
        graph_versions=(a.graph_version, b.graph_version),
    )
