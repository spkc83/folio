"""Confirm, rename, and certify proposals from discovery and import.

Confirmation goes in kind order (Source, Dataset, Field, Relationship, MetricBinding), so
the derived-edge listener never meets a missing endpoint. One item confirmed out of order
fails with "Confirm x first" and writes nothing.
"""
import json
import re

from pydantic import BaseModel

from folio_graph import GraphError, Node, NodeChange, Proposal
from living_reports.discovery.run import KIND_ORDER, is_model_proposal, order_key, run_id_of
from living_reports.reports import changes
from living_reports.reports.lineage import OWN, proposers_of, record
from living_reports.reports.signoff import Forbidden
from living_reports.sources.registry import active_source
from living_reports.store import Store

__all__ = ["KIND_ORDER", "ConfirmResult", "certify_metric", "confirm_proposals", "rename_proposal"]

RENAMEABLE = ("Field", "MetricBinding")
MAX_LABEL = 80
NOT_MODEL = "Not a model proposal"
NO_SUCH = "No such proposal"
_MISSING = re.compile(r"refers to (.+?), which is not in the graph")


class ConfirmResult(BaseModel):
    confirmed: list[str]
    failed: list[tuple[str, str]]


def _why(error: GraphError) -> str:
    found = _MISSING.search(str(error))
    if found:
        return f"Confirm {found.group(1).split(', ')[0]} first"
    return str(error)


def _context(p: Proposal, by: str, store: Store):
    """The ask context on the source of the run that made ``p``."""
    from living_reports.harness.builder import AskContext
    from living_reports.reports.store import ReportStore

    row = store.row("SELECT source_id FROM discovery_runs WHERE id = ?", (run_id_of(p),))
    source_id = row[0] if row else active_source(store)
    return AskContext(store=store, reports=ReportStore(store), source_id=source_id, user=by)


def confirm_proposals(ids: list[str] | None, by: str, store: Store) -> ConfirmResult:
    """Confirm ``ids`` (``None``: every pending discovery or import proposal) in kind order.

    Each proposal has its own transaction; a failure rolls back only that one. The caller
    checks the owner role.
    """
    fg = store.graph
    if ids is None:
        chosen = [p for p in fg.pending() if is_model_proposal(p)]
        missing: list[str] = []
    else:
        found = {pid: fg.proposal(pid) for pid in dict.fromkeys(ids)}
        chosen = [p for p in found.values() if p is not None]
        missing = [pid for pid, p in found.items() if p is None]
    result = ConfirmResult(confirmed=[], failed=[(pid, NO_SUCH) for pid in missing])
    todo = sorted(chosen, key=order_key)
    # Kind order puts every endpoint first, except a metric built on another metric (a
    # ratio): one more pass confirms what waited on an item confirmed in this call.
    while todo:
        waiting: list[tuple[Proposal, str]] = []
        progress = False
        for p in todo:
            if not is_model_proposal(p):
                result.failed.append((p.id, NOT_MODEL))
            elif p.status != "pending":
                result.failed.append((p.id, "This proposal is already closed."))
            elif by in proposers_of(store, p):
                result.failed.append((p.id, OWN))
            else:
                try:
                    changes.confirm_proposal(p, by, store, lambda p=p: _context(p, by, store))
                except GraphError as error:
                    waiting.append((p, _why(error)))
                    continue
                except Forbidden as error:
                    result.failed.append((p.id, str(error)))
                    continue
                result.confirmed.append(p.id)
                progress = True
        if not progress or not waiting:
            result.failed.extend((p.id, why) for p, why in waiting)
            break
        todo = [p for p, _ in waiting]
    return result


def _base(reason: str) -> str:
    """``discovery:<run>`` or ``import:<run>``, without a note."""
    return reason.split(" ", 1)[0]


def rename_proposal(pid: str, label: str, by: str, store: Store) -> Proposal:
    """Replace a pending Field or metric proposal with one whose ``label`` is ``label``.

    The id does not change (plan ruling 4). ``by`` changed the content, so ``by`` is the
    proposer of the new proposal; the lineage keeps every earlier proposer too, and none
    of them can confirm it (four eyes). The reason keeps the
    run and names the original proposer; the old proposal closes as rejected by ``by``
    with the note "renamed to ...".
    """
    fg = store.graph
    old = fg.proposal(pid)
    if old is None:
        raise LookupError("No such proposal")
    if not is_model_proposal(old):
        raise ValueError(NOT_MODEL)
    if old.change.kind not in RENAMEABLE:
        raise ValueError("Only fields and metrics can be renamed")
    label = label.strip()
    if not label or len(label) > MAX_LABEL:
        raise ValueError(f"A label has 1 to {MAX_LABEL} characters")
    if old.status != "pending":
        raise GraphError("This proposal is already closed.")
    change = old.change.model_copy(update={"payload": {**old.change.payload, "label": label}})
    with store.transaction():
        fg.reject(pid, by, f"renamed to {label}")
        lineage = proposers_of(store, old)
        reason = f"{_base(old.reason)} (renamed by {by}; proposed by {lineage[0]})"
        new = fg.propose(change, by, reason)
        record(store, new.id, [*lineage, by])
        store.execute(
            "UPDATE discovery_probes SET proposal_id = ? WHERE proposal_id = ?", (new.id, pid)
        )
    return new


def certify_metric(metric_id: str, by: str, store: Store) -> Node:
    """An owner approves the SQL of an uncertified expression metric (spec 11.3)."""
    fg = store.graph
    node = fg.slice().node(metric_id)
    if node is None or node.kind != "MetricBinding":
        raise ValueError(f"No metric {metric_id}")
    if node.payload.get("expression") is None:
        raise ValueError("Only an expression metric needs certification")
    if node.payload.get("certified", True):
        raise ValueError(f"{metric_id} is already certified")
    payload = {**node.payload, "certified": True}
    return fg.put(metric_id, "MetricBinding", payload, by=by, reason="SQL approved by an owner")
