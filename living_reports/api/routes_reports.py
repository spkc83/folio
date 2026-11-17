"""Report read routes."""
from fastapi import APIRouter, Depends, HTTPException

from living_reports.api.deps import Stores, stores
from living_reports.engine.query import upgrade_definition
from living_reports.fixtures import fraud_overview
from living_reports.reports.certified import verified_for
from living_reports.reports.payload import ReportPayload, query_view
from living_reports.spec.models import VIEW_FOR_OBJECTIVE

router = APIRouter(prefix="/api")


def engine_defs(defs: dict, model) -> dict:
    """Stored query definitions in the engine's form. Reports saved before it are upgraded."""
    return {ref: upgrade_definition(d, model) for ref, d in defs.items()}


@router.get("/reports")
def list_reports(s: Stores = Depends(stores)) -> list[dict]:
    return [
        {"id": r.id, "title": r.spec.title, "tier": r.tier, "author": r.author}
        for r in s.reports.list_reports()
    ]


@router.get("/reports/{report_id}", response_model=ReportPayload)
def get_report(report_id: str, s: Stores = Depends(stores)) -> ReportPayload:
    stored = s.reports.get(report_id)
    if stored is not None:
        return ReportPayload(
            id=stored.id,
            spec=stored.spec,
            facts=stored.facts,
            queries={k: query_view(k, v) for k, v in stored.queries.items()},
            tier=stored.tier,
            view=stored.view,
            cost=stored.cost,
            cube=stored.cube,
            cube_reason=stored.cube_reason,
            query_defs=engine_defs(stored.query_defs, s.graph.model()),
            verified=verified_for(s.store, stored.id),
            author=stored.author,
            contributors=stored.contributors,
        )
    if report_id != fraud_overview.REPORT_ID:
        raise HTTPException(status_code=404, detail="No such report")
    report = fraud_overview.build(s.store)
    return ReportPayload(
        id=report_id,
        spec=report.spec,
        facts=report.facts,
        queries={k: query_view(k, v) for k, v in report.queries.items()},
        view=VIEW_FOR_OBJECTIVE[report.spec.objective],
    )
