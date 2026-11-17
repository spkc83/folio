"""Home routes: the certified question set, search, and the weekly digest."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from living_reports.api.deps import Stores, stores
from living_reports.config import get_settings
from living_reports.harness.builder import AskContext
from living_reports.reports.certified import CertifiedQuestion, list_certified
from living_reports.reports.digest import Digest, build_digest
from living_reports.reports.search import SearchHit, search_reports

router = APIRouter(prefix="/api")


@router.get("/certified", response_model=list[CertifiedQuestion])
def certified(s: Stores = Depends(stores)) -> list[CertifiedQuestion]:
    return list_certified(s.store)


@router.get("/search", response_model=list[SearchHit])
def search(q: str = Query("", max_length=200), s: Stores = Depends(stores)) -> list[SearchHit]:
    return search_reports(s.store, q)


@router.get("/digest", response_model=Digest)
def digest(s: Stores = Depends(stores)) -> Digest:
    ctx = s.context()
    return build_digest(ctx)


@router.get("/config")
def config() -> dict[str, Any]:
    """The deployment's flags and the demo's example questions. No user header is needed:
    the app reads this first."""
    from living_reports.demos import get_demo
    from living_reports.demos.switch import datasets

    settings = get_settings()
    demo = get_demo(settings.demo)
    return {"demo": settings.demo_mode, "placeholder": demo.placeholder,
            "starters": demo.starters, "dataset": settings.demo, "datasets": datasets()}


class DemoChoice(BaseModel):
    name: str


@router.post("/demo")
def select_demo(choice: DemoChoice) -> dict[str, Any]:
    """Select the demo dataset for the whole server. The first selection of a dataset
    generates its data, so the request can take some time."""
    from living_reports.demos.switch import DemoSwitchError, switch_demo

    try:
        switch_demo(choice.name)
    except DemoSwitchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return config()
