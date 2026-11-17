"""The model page: discovery, the proposals it made, confirmation, rename, and certify.

Every change is owner-only. Discovery and import propose; a person confirms.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from folio_graph import GraphError, Proposal
from living_reports.api.auth import role_of
from living_reports.api.deps import Stores, stores
from living_reports.api.routes_graph import RejectBody, _pending
from living_reports.discovery.confirm import (
    NO_SUCH,
    NOT_MODEL,
    ConfirmResult,
    certify_metric,
    confirm_proposals,
    rename_proposal,
)
from living_reports.discovery.run import (
    DiscoveryRun,
    ProposalView,
    discover,
    fresh_catalog,
    is_model_proposal,
    list_pending,
    propose_changes,
)
from living_reports.harness.builder import INVALID, UNAVAILABLE, AskContext
from living_reports.harness.llm import InvalidModelOutput, ModelUnavailable
from living_reports.reports import changes
from living_reports.reports.signoff import Conflict, Forbidden
from living_reports.semantic.kinds import SourcePayload
from living_reports.semantic.ossie import (
    ImportResult,
    document_to_changes,
    export_model,
    parse_document,
)
from living_reports.sources import SourceError
from living_reports.sources.registry import source_for, sources

router = APIRouter(prefix="/api/model")


class DiscoverBody(BaseModel):
    source_id: str


class ConfirmBody(BaseModel):
    ids: list[str] | None = None


class RenameBody(BaseModel):
    label: str


class ImportBody(BaseModel):
    source_id: str
    document: str


class CertifyResult(BaseModel):
    id: str
    version: int
    certified: bool


def owner(s: Stores) -> None:
    if role_of(s.user) != "owner":
        raise Forbidden("Only an owner can change the model.")


def context_for(source_id: str, s: Stores) -> AskContext:
    if source_id not in sources():
        raise HTTPException(status_code=404, detail=f"No source is configured for {source_id}")
    return AskContext(store=s.store, reports=s.reports, source_id=source_id, user=s.user)


@router.post("/discover", response_model=DiscoveryRun)
def run_discovery(body: DiscoverBody, s: Stores = Depends(stores)) -> DiscoveryRun:
    owner(s)
    ctx = context_for(body.source_id, s)
    try:
        return discover(body.source_id, ctx)
    except ModelUnavailable as error:
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from error
    except InvalidModelOutput as error:
        raise HTTPException(status_code=502, detail=INVALID) from error
    except SourceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (ValueError, GraphError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/proposals", response_model=list[ProposalView])
def proposals(s: Stores = Depends(stores)) -> list[ProposalView]:
    return list_pending(s.store)


@router.post("/proposals/confirm", response_model=ConfirmResult)
def confirm(body: ConfirmBody, s: Stores = Depends(stores)) -> ConfirmResult:
    owner(s)
    result = confirm_proposals(body.ids, s.user, s.store)
    if body.ids is not None and len(body.ids) == 1 and result.failed:
        why = result.failed[0][1]
        raise HTTPException(status_code=404 if why == NO_SUCH else 409, detail=why)
    return result


@router.post("/proposals/{proposal_id}/rename", response_model=Proposal)
def rename(proposal_id: str, body: RenameBody, s: Stores = Depends(stores)) -> Proposal:
    owner(s)
    try:
        return rename_proposal(proposal_id, body.label, s.user, s.store)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="No such proposal") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except GraphError as error:
        raise Conflict("This proposal is already closed.") from error


@router.post("/proposals/{proposal_id}/reject", response_model=Proposal)
def reject(proposal_id: str, body: RejectBody, s: Stores = Depends(stores)) -> Proposal:
    owner(s)
    proposal = s.graph.proposal(proposal_id)
    if proposal is not None and not is_model_proposal(proposal):
        raise HTTPException(status_code=409, detail=NOT_MODEL)
    try:
        return changes.reject_proposal(_pending(proposal_id, s), s.user, body.reason, s.store)
    except GraphError as error:
        raise Conflict("This proposal is already closed.") from error


@router.post("/metrics/{metric_id}/certify", response_model=CertifyResult)
def certify(metric_id: str, s: Stores = Depends(stores)) -> CertifyResult:
    owner(s)
    try:
        node = certify_metric(metric_id, s.user, s.store)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return CertifyResult(id=node.id, version=node.version, certified=True)


def attachment_name(name: str) -> str:
    """A file name safe inside a quoted Content-Disposition value."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) + ".ossie.yaml"


@router.get("/export")
def export(source_id: str | None = None, s: Stores = Depends(stores)) -> Response:
    """The confirmed model as an Ossie document."""
    model = s.graph.model()
    if not model.datasets or (source_id is not None and model.source != source_id):
        raise HTTPException(status_code=404, detail="No confirmed model for that source.")
    headers = {"Content-Disposition": f'attachment; filename="{attachment_name(model.name)}"'}
    return Response(export_model(model), media_type="text/yaml", headers=headers)


@router.post("/import", response_model=ImportResult)
def import_document(body: ImportBody, s: Stores = Depends(stores)) -> ImportResult:
    """An Ossie document becomes proposals; the probes run as in discovery."""
    owner(s)
    ctx = context_for(body.source_id, s)
    try:
        source = source_for(body.source_id)
        doc = parse_document(body.document)
        payload = SourcePayload(
            name=body.source_id, dialect=source.dialect, supports_as_of=source.supports_as_of
        )
        changes, dropped = document_to_changes(doc, payload, fresh_catalog(source))
        # The importer is the proposer: another owner confirms (four eyes, as for rename).
        run = propose_changes(changes, body.source_id, ctx, by=s.user, reason_prefix="import",
                              dropped=dropped, note=f"by {s.user}")
    except SourceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (ValueError, GraphError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ImportResult(run=run, dropped=run.dropped)
