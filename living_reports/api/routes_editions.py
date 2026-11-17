"""Edition reads and exports."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from living_reports.api.deps import Stores, stores
from living_reports.config import get_settings
from living_reports.reports.certified import verified_for_edition
from living_reports.reports.editions import Edition, EditionStore, payload_of, render_export
from living_reports.reports.payload import ReportPayload

router = APIRouter(prefix="/api")


def _edition(report_id: str, number: int, s: Stores) -> Edition:
    edition = s.editions.get(report_id, number)
    if edition is None:
        raise HTTPException(status_code=404, detail="No such edition")
    return edition


@router.get("/reports/{report_id}/editions/{number}", response_model=ReportPayload)
def get_edition(report_id: str, number: int, s: Stores = Depends(stores)) -> ReportPayload:
    return _with_verified(payload_of(_edition(report_id, number, s)), s)


@router.get("/reports/{report_id}/latest", response_model=ReportPayload)
def latest(report_id: str, s: Stores = Depends(stores)) -> ReportPayload:
    edition = s.editions.latest(report_id)
    if edition is None:
        raise HTTPException(status_code=404, detail="No published edition")
    return _with_verified(payload_of(edition), s)


def _with_verified(payload: ReportPayload, s: Stores) -> ReportPayload:
    """Only the edition that was published and certified carries the badge."""
    verified = verified_for_edition(s.store, payload.id, payload.edition)
    return payload.model_copy(update={"verified": verified})


@router.get("/reports/{report_id}/editions/{number}/export")
def export(report_id: str, number: int, s: Stores = Depends(stores)) -> HTMLResponse:
    edition = _edition(report_id, number, s)
    if edition.tier == "exploratory":
        raise HTTPException(status_code=409, detail="An exploratory report has no export")
    if edition.status != "published":
        # A file cannot be recalled after it is shared, so only published content leaves.
        raise HTTPException(status_code=409, detail="Publish this edition before you export it")
    settings = get_settings()
    template = Path(settings.export_template)
    if not template.exists():
        raise HTTPException(status_code=503, detail="The export page needs a web build")
    body = payload_of(edition).model_dump(mode="json")
    if body.get("cube"):
        # The file drills only by the dimensions that its cube carries. The graph stays home.
        body["cube"]["vocabulary"] = {}
    for section in body.get("spec", {}).get("sections", []):
        # A follow-up chip names other members of the model, and a file cannot ask anyway.
        section["follow_ups"] = []
    body["hosted_url"] = f"{settings.public_url}/r/{report_id}/e/{number}"
    html = render_export(body, template.read_text())
    name = f"report-{report_id}-edition-{number}.html"
    return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="{name}"'})
