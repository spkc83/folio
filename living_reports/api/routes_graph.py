"""Governed changes to the context graph."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from folio_graph import GraphError, NodeChange, Proposal
from living_reports.api.auth import role_of
from living_reports.api.deps import Stores, stores
from living_reports.engine.errors import QueryRejected
from living_reports.engine.expressions import parse_expression
from living_reports.harness.builder import AskContext
from living_reports.reports import changes
from living_reports.reports.changes import Impact, NoImpactPreview, impact
from living_reports.reports.lineage import OWN, proposers_of
from living_reports.reports.signoff import Conflict, Forbidden

router = APIRouter(prefix="/api/graph")


# The kinds a person may propose over the API. Other kinds come from code paths only.
PROPOSABLE = ("MetricBinding", "Field")


def _check_proposable(body: "ProposeBody") -> None:
    if body.kind not in PROPOSABLE:
        raise HTTPException(
            status_code=422,
            detail=f"A {body.kind} cannot be proposed here. Use one of: {', '.join(PROPOSABLE)}.",
        )
    if body.kind == "Field" and body.payload.get("expression") is not None:
        raise HTTPException(
            status_code=422, detail="A Field expression cannot be proposed here."
        )


class ProposeBody(BaseModel):
    node_id: str | None = None
    kind: str
    payload: dict
    reason: str


class RejectBody(BaseModel):
    reason: str


def _context(s: Stores) -> AskContext:
    return s.context()


def _pending(proposal_id: str, s: Stores) -> Proposal:
    proposal = s.graph.proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="No such proposal")
    if role_of(s.user) != "owner":
        raise Forbidden("Only an owner can decide on a proposal.")
    if s.user in proposers_of(s.store, proposal):
        raise Forbidden(OWN)
    if proposal.status != "pending":
        raise Conflict("This proposal is already closed.")
    return proposal


@router.get("/proposals", response_model=list[Proposal])
def list_proposals(s: Stores = Depends(stores)) -> list[Proposal]:
    return s.graph.pending()


def _node_id(body: ProposeBody) -> str:
    """The id follows from the payload: a Field is ``dataset.name``, a metric is its name."""
    payload = body.payload
    if body.kind == "Field":
        node_id = f"{payload.get('dataset')}.{payload.get('name')}"
    else:
        node_id = str(payload.get("name"))
    if body.node_id is not None and body.node_id != node_id:
        raise HTTPException(
            status_code=422,
            detail=f"node_id {body.node_id!r} does not match the payload, which gives {node_id!r}.",
        )
    return node_id


def _parsed_expression(body: ProposeBody, s: Stores) -> dict:
    """The payload with its expression metric parsed on the server.

    The client's ``ast`` and ``fields`` are never trusted: the server parses ``sql`` with
    sqlglot against the live model and stores that AST.
    """
    payload = body.payload
    given = payload.get("expression") if body.kind == "MetricBinding" else None
    if given is None:
        return payload
    if not isinstance(given, dict) or not isinstance(given.get("sql"), str):
        raise HTTPException(status_code=422, detail="An expression metric needs its SQL text.")
    try:
        parsed = parse_expression(
            given["sql"],
            str(given.get("dataset")),
            s.graph.model(),
            str(given.get("dialect") or "duckdb"),
        )
    except QueryRejected as error:
        raise HTTPException(status_code=422, detail=error.reason) from error
    return {**payload, "expression": parsed.model_dump(mode="json")}


@router.post("/proposals", response_model=Proposal)
def propose(body: ProposeBody, s: Stores = Depends(stores)) -> Proposal:
    _check_proposable(body)
    payload = _parsed_expression(body, s)
    change = NodeChange(id=_node_id(body), kind=body.kind, payload=payload)
    try:
        return s.graph.propose(change, by=s.user, reason=body.reason)
    except GraphError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/proposals/{proposal_id}/confirm", response_model=Proposal)
def confirm(proposal_id: str, s: Stores = Depends(stores)) -> Proposal:
    proposal = _pending(proposal_id, s)
    try:
        changes.confirm_proposal(proposal, s.user, s.store, lambda: _context(s))
    except GraphError as error:
        if "already" in str(error):
            raise Conflict("This proposal is already closed.") from error
        raise HTTPException(status_code=422, detail=str(error)) from error
    return s.graph.proposal(proposal.id)


@router.get("/proposals/{proposal_id}/impact", response_model=Impact)
def proposal_impact(proposal_id: str, s: Stores = Depends(stores)) -> Impact:
    proposal = s.graph.proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="No such proposal")
    try:
        return impact(proposal, _context(s))
    except NoImpactPreview as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/proposals/{proposal_id}/reject", response_model=Proposal)
def reject(proposal_id: str, body: RejectBody, s: Stores = Depends(stores)) -> Proposal:
    try:
        return changes.reject_proposal(_pending(proposal_id, s), s.user, body.reason, s.store)
    except GraphError as error:
        raise Conflict("This proposal is already closed.") from error
