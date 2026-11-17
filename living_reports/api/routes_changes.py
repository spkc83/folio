"""Sources, the change briefing, and edition differences."""
from fastapi import APIRouter, Depends, HTTPException, Query

from living_reports.api.deps import Stores, stores
from living_reports.config import get_settings
from living_reports.harness.builder import AskContext
from living_reports.reports.changes import (
    ChangeItem,
    EditionDiff,
    advance_demo,
    diff_editions,
    open_changes,
)
from living_reports.semantic.demo import DEMO_SOURCE
from living_reports.sources import SourceError
from living_reports.sources.build import demo_available, demo_moment
from living_reports.sources.registry import source_for, sources

router = APIRouter(prefix="/api")


def _context(s: Stores) -> AskContext:
    return s.context()


def _describe(source_id: str) -> dict:
    """One source, or its id and the reason it cannot open (no path or DSN in it)."""
    try:
        source = source_for(source_id)
    except SourceError as error:
        return {"id": source_id, "error": str(error)}
    try:
        data_time = source.data_time().isoformat()
    except SourceError:
        data_time = None
    return {
        "id": source_id,
        "dialect": source.dialect,
        "supports_as_of": source.supports_as_of,
        "data_time": data_time,
    }


@router.get("/sources")
def list_sources(s: Stores = Depends(stores)) -> dict:
    """The configured sources, and the demo moment when the demo source exists."""
    ids = sources()
    return {
        "sources": [_describe(i) for i in ids],
        "demo_moment": demo_moment(s.store) if DEMO_SOURCE in ids else None,
    }


@router.post("/sources/{source_id}/advance", response_model=list[ChangeItem])
def advance(source_id: str, s: Stores = Depends(stores)) -> list[ChangeItem]:
    """Demo only: load the next moment of the demo data and brief the changes."""
    if source_id != DEMO_SOURCE or not demo_available():
        raise HTTPException(status_code=404, detail="No such demo source")
    if demo_moment(s.store) is None:
        raise HTTPException(
            status_code=503, detail="The demo source is not built yet. Restart the API to build it."
        )
    try:
        return advance_demo(s.context(DEMO_SOURCE))
    except SourceError as error:
        raise HTTPException(
            status_code=503, detail=f"The data source {DEMO_SOURCE} is not available."
        ) from error


@router.get("/changes", response_model=list[ChangeItem])
def changes(s: Stores = Depends(stores)) -> list[ChangeItem]:
    return open_changes(_context(s))


@router.get("/reports/{report_id}/diff", response_model=EditionDiff)
def diff(
    report_id: str,
    start: int = Query(alias="from"),
    end: int = Query(alias="to"),
    s: Stores = Depends(stores),
) -> EditionDiff:
    store = s.editions
    a, b = store.get(report_id, start), store.get(report_id, end)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="No such edition")
    return diff_editions(a, b, _context(s))
