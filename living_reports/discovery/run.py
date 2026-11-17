"""A discovery run: introspect, draft, probe, and propose. Nothing is confirmed here."""
import json
import uuid
from datetime import datetime

from pydantic import BaseModel

from folio_graph import NodeChange, Proposal
from living_reports.discovery.candidates import relationship_candidates
from living_reports.discovery.draft import DiscoveryDraft, dataset_tables
from living_reports.discovery.preview import model_from_changes
from living_reports.discovery.probe import Probe, probe_changes
from living_reports.discovery.propose import (
    Dropped,
    draft_model,
    to_changes,
    with_relationship_keys,
)
from living_reports.reports.lineage import record
from living_reports.semantic.kinds import SourcePayload
from living_reports.sources.base import Catalog
from living_reports.sources.registry import source_for
from living_reports.store import Store

KIND_ORDER = ("Source", "Dataset", "Field", "Relationship", "MetricBinding")
PREFIXES = ("discovery:", "import:")


class DiscoveryRun(BaseModel):
    id: str
    source_id: str
    started_at: datetime
    by: str
    proposal_ids: list[str]
    dropped: list[Dropped]
    skipped: list[str]


class ProposalView(BaseModel):
    proposal: Proposal
    probe: Probe | None
    run_id: str | None
    kind: str
    node_id: str
    summary: str


def fresh_catalog(source) -> Catalog:
    """The catalog read again now. A source that keeps its catalog (Postgres) drops it
    first, so a discovery run or an import sees new tables and columns."""
    refresh = getattr(source, "refresh", None)
    if refresh is not None:
        refresh()
    return source.introspect()


def discover(
    source_id: str, ctx, *, by: str = "discovery", draft: DiscoveryDraft | None = None
) -> DiscoveryRun:
    """Propose a mapping layer for ``source_id``. ``ctx`` is an ``AskContext``.

    The model drafts from the catalog unless ``draft`` is given. ``ModelUnavailable`` and
    ``InvalidModelOutput`` reach the caller.
    """
    source = source_for(source_id)
    catalog = fresh_catalog(source)
    if not dataset_tables(catalog):
        raise ValueError(f"{source_id} has no table with a column that Folio can read.")
    if draft is None:
        draft = draft_model(catalog, relationship_candidates(catalog), ctx.tier, ctx.turn_id)
    payload = SourcePayload(
        name=source_id, dialect=source.dialect, supports_as_of=source.supports_as_of
    )
    changes, dropped = to_changes(draft, catalog, payload)
    return propose_changes(changes, source_id, ctx, by=by, reason_prefix="discovery",
                           dropped=dropped)


def propose_changes(
    changes: list[NodeChange],
    source_id: str,
    ctx,
    *,
    by: str,
    reason_prefix: str,
    dropped: list[Dropped] | None = None,
    note: str = "",
) -> DiscoveryRun:
    """Probe ``changes`` on the source, drop the failures, and propose the rest.

    A change whose live node already has the same payload, or that a pending proposal
    already holds, is skipped. One transaction writes the proposals, probes, and the run.
    """
    store: Store = ctx.store
    fg = store.graph
    source = source_for(source_id)
    live = fg.slice()
    changes, foreign = _keep_to_source(changes, source_id, live)
    dropped = [*(dropped or []), *foreign]
    known = fg.model() if live.node(source_id) is not None else None
    claimed = with_relationship_keys(changes)
    probes = probe_changes(claimed, model_from_changes(claimed, known), source)
    kinds = {c.id: c.kind for c in changes}
    failed = {k: p for k, p in probes.items() if p.status == "fail"}
    dropped = [
        *dropped,
        *(Dropped(name=k, kind=kinds[k], reason=p.detail) for k, p in failed.items()),
    ]
    final = with_relationship_keys([c for c in changes if c.id not in failed])
    run = DiscoveryRun(
        id=uuid.uuid4().hex[:12], source_id=source_id, started_at=store.now(), by=by,
        proposal_ids=[], dropped=dropped, skipped=[],
    )
    reason = f"{reason_prefix}:{run.id}" + (f" ({note})" if note else "")
    registry = fg.graph.registry
    with store.transaction():
        held = {
            (p.change.id, p.change.kind): p.change.payload
            for p in fg.pending()
            if isinstance(p.change, NodeChange)
        }
        for c in final:
            payload = registry.validate(c)
            node = live.node(c.id)
            if (node is not None and node.kind == c.kind and node.payload == payload) or held.get(
                (c.id, c.kind)
            ) == payload:
                run.skipped.append(c.id)
                continue
            proposal = fg.propose(NodeChange(id=c.id, kind=c.kind, payload=payload), by, reason)
            run.proposal_ids.append(proposal.id)
            record(store, proposal.id, [by])
            if c.id in probes:
                store.execute(
                    "INSERT INTO discovery_probes VALUES (?, ?, ?)",
                    (proposal.id, run.id, probes[c.id].model_dump_json()),
                )
        store.execute(
            "INSERT INTO discovery_runs VALUES (?, ?, ?, ?)",
            (run.id, source_id, store.ts(run.started_at), run.model_dump_json()),
        )
    return run


def _datasets_of(c: NodeChange) -> list[str]:
    """The datasets that a change depends on."""
    p = c.payload
    if c.kind == "Dataset":
        return [c.id]
    if c.kind == "Field":
        return [p["dataset"]]
    if c.kind == "Relationship":
        return [p["from_dataset"], p["to_dataset"]]
    if c.kind == "MetricBinding":
        refs = []
        if p.get("measure"):
            m = p["measure"]
            refs += [m.get("dataset")] + [x.split(".")[0] for x in (m.get("field"),
                     (m.get("filter") or {}).get("member")) if x]
        if p.get("expression"):
            refs.append(p["expression"]["dataset"])
        return [r for r in refs if r]
    return []


def _keep_to_source(
    changes: list[NodeChange], source_id: str, live
) -> tuple[list[NodeChange], list[Dropped]]:
    """Drop what would take a live node from another source or change a live node's kind.

    A dataset of another source keeps its source: a second source never re-points it, and
    no field, relationship, or metric of this run builds on it.
    """
    foreign: dict[str, str] = {}
    for c in changes:
        node = live.node(c.id)
        if c.kind == "Dataset" and node is not None and node.kind == "Dataset":
            owner = node.payload.get("source_id")
            if owner != source_id:
                foreign[c.id] = owner
    kept, dropped = [], []
    for c in changes:
        node = live.node(c.id)
        on = [d for d in _datasets_of(c) if d in foreign]
        if on:
            dropped.append(Dropped(name=c.id, kind=c.kind,
                                   reason=f"{on[0]} belongs to source {foreign[on[0]]}"))
        elif node is not None and node.kind != c.kind:
            dropped.append(Dropped(name=c.id, kind=c.kind, reason=f"name collides with {node.kind}"))
        else:
            kept.append(c)
    return kept, dropped


def _cols(cols: list[str]) -> str:
    return ",".join(cols)


def summary(kind: str, p: dict) -> str:
    """One line that says what a proposed node is."""
    if kind == "Source":
        return p["dialect"]
    if kind == "Dataset":
        key = f"key {', '.join(p['primary_key'])}" if p["primary_key"] else "no declared key"
        return f"{p['table']}, {key}"
    if kind == "Field":
        dim = p.get("dimension")
        tag = "" if dim is None else (", time" if dim.get("is_time") else ", dimension")
        return f"{p['type']}{tag}"
    if kind == "Relationship":
        return (
            f"{p['from_dataset']}.{_cols(p['from_columns'])} → "
            f"{p['to_dataset']}.{_cols(p['to_columns'])}"
        )
    if kind == "MetricBinding":
        if p.get("measure"):
            m = p["measure"]
            text = f"{m['agg']}({m.get('field') or m.get('dataset')})"
            if m.get("filter"):
                f = m["filter"]
                values = ", ".join(str(v) for v in f.get("values", []))
                text += f" filter {f['member']} {f['op']} {values}".rstrip()
        elif p.get("ratio"):
            text = f"ratio {p['ratio']['numerator']} / {p['ratio']['denominator']}"
        else:
            text = f"expression: {p['expression']['sql']}"
        return text if p.get("certified", True) else f"{text} (uncertified)"
    return kind


def _probes(store: Store) -> dict[str, Probe]:
    rows = store.rows("SELECT proposal_id, payload FROM discovery_probes")
    return {pid: Probe.model_validate_json(payload) for pid, payload in rows}


def is_model_proposal(p: Proposal) -> bool:
    return isinstance(p.change, NodeChange) and p.reason.startswith(PREFIXES)


def run_id_of(p: Proposal) -> str | None:
    """The run id in a reason such as ``discovery:<run id>`` or ``import:<run id> (...)``."""
    rest = p.reason.split(":", 1)[1] if ":" in p.reason else ""
    return rest.split(" ", 1)[0] or None


def order_key(p: Proposal) -> tuple[int, str]:
    kind = p.change.kind
    return (KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER), p.change.id)


def list_pending(store: Store) -> list[ProposalView]:
    """Pending discovery and import proposals, in confirmation order."""
    probes = _probes(store)
    chosen = sorted((p for p in store.graph.pending() if is_model_proposal(p)), key=order_key)
    return [
        ProposalView(
            proposal=p,
            probe=probes.get(p.id),
            run_id=run_id_of(p),
            kind=p.change.kind,
            node_id=p.change.id,
            summary=summary(p.change.kind, p.change.payload),
        )
        for p in chosen
    ]
