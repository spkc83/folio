"""Publication routes."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from living_reports.api.deps import Stores, stores
from living_reports.reports.editions import EditionStore
from living_reports.reports.payload import Tier
from living_reports.reports.signoff import EditionState, Signoff

router = APIRouter(prefix="/api")


class SignBody(BaseModel):
    content_hash: str


class TierBody(BaseModel):
    tier: Tier


class WordingBody(BaseModel):
    note: str


def _signoff(s: Stores) -> Signoff:
    return s.signoff


def _require(report_id: str, s: Stores) -> None:
    if s.reports.get(report_id) is None:
        raise HTTPException(status_code=404, detail="No such report")


@router.post("/reports/{report_id}/tier")
def set_tier(report_id: str, body: TierBody, s: Stores = Depends(stores)) -> dict[str, str]:
    _require(report_id, s)
    _signoff(s).set_tier(report_id, body.tier, s.user)
    return {"tier": body.tier}


@router.post("/reports/{report_id}/editions", response_model=EditionState)
def create_edition(report_id: str, s: Stores = Depends(stores)) -> EditionState:
    _require(report_id, s)
    return _signoff(s).freeze_and_check(report_id)


class Publication(BaseModel):
    edition: EditionState | None
    draft_changed: bool


@router.get("/reports/{report_id}/publication", response_model=Publication)
def publication(report_id: str, s: Stores = Depends(stores)) -> Publication:
    report = s.reports.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="No such report")
    signoff = _signoff(s)
    newest = signoff.editions.newest(report_id)
    if newest is None:
        return Publication(edition=None, draft_changed=True)
    changed = signoff.editions.draft_hash(report) != newest.content_hash
    return Publication(edition=signoff.state(newest), draft_changed=changed)


@router.get("/reports/{report_id}/editions/{number}/state", response_model=EditionState)
def edition_state(report_id: str, number: int, s: Stores = Depends(stores)) -> EditionState:
    edition = s.editions.get(report_id, number)
    if edition is None:
        raise HTTPException(status_code=404, detail="No such edition")
    return _signoff(s).state(edition)


@router.post("/reports/{report_id}/editions/{number}/sign", response_model=EditionState)
def sign(report_id: str, number: int, body: SignBody, s: Stores = Depends(stores)) -> EditionState:
    return _signoff(s).sign(report_id, number, body.content_hash, s.user)


@router.post("/reports/{report_id}/editions/{number}/publish", response_model=EditionState)
def publish(report_id: str, number: int, s: Stores = Depends(stores)) -> EditionState:
    return _signoff(s).publish(report_id, number, s.user)


@router.post("/reports/{report_id}/editions/{number}/wording", response_model=EditionState)
def wording(
    report_id: str, number: int, body: WordingBody, s: Stores = Depends(stores)
) -> EditionState:
    return _signoff(s).resolve_wording(report_id, number, body.note, s.user)
