"""Drill and pin: the two server calls of deterministic exploration.

Both read the report's query definitions through ``reports.explore`` and run on the query
engine. The request model ``ScopedQuery`` and the ``QueryView`` response are the contract with
the web app.
"""
from fastapi import APIRouter, Depends, HTTPException

from living_reports.api.deps import Stores, stores
from living_reports.engine import OutOfScope, Query, QueryRejected
from living_reports.harness.builder import AskResult, answer
from living_reports.metrics.runner import run_query
from living_reports.reports.explore import ScopedQuery, describe, scoped_query
from living_reports.reports.payload import QueryView, query_view
from living_reports.reports.store import StoredReport
from living_reports.semantic.model import SemanticModel
from living_reports.sources import SourceError

router = APIRouter(prefix="/api")


def _scoped(report_id: str, sq: ScopedQuery, s: Stores) -> tuple[StoredReport, Query, SemanticModel]:
    report = s.reports.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="No such report")
    if sq.ref not in report.query_defs:
        raise HTTPException(status_code=422, detail=f"The report has no query {sq.ref}.")
    model = s.graph.model()
    if sq.by is not None:
        member = model.members.get(sq.by)
        if member is None or member.dimension is None:
            raise HTTPException(status_code=422, detail=f"The model has no dimension {sq.by}.")
    try:
        return report, scoped_query(report.query_defs, sq, model), model
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _labels(query: Query, model: SemanticModel) -> dict[str, str]:
    labels = {m.id: m.label for m in model.metrics if m.id in query.metrics}
    return labels | {i: f.label for i, f in model.members.items()}


@router.post("/reports/{report_id}/drill", response_model=QueryView)
def drill(report_id: str, sq: ScopedQuery, s: Stores = Depends(stores)) -> QueryView:
    report, query, _ = _scoped(report_id, sq, s)
    try:
        result = run_query(query, report.source_id, s.store)
    except (OutOfScope, QueryRejected) as error:
        raise HTTPException(status_code=422, detail=error.reason) from error
    except SourceError as error:
        raise HTTPException(
            status_code=503, detail=f"The data source {report.source_id} is not available."
        ) from error
    return query_view(sq.ref, result)


@router.post("/reports/{report_id}/pin", response_model=AskResult)
def pin(report_id: str, sq: ScopedQuery, s: Stores = Depends(stores)) -> AskResult:
    """Write the exploration as a new draft section. It needs checks and signatures again."""
    report, query, model = _scoped(report_id, sq, s)
    intent = describe(report.query_defs, sq, _labels(query, model), model)
    choice = query.model_dump(mode="json", by_alias=True, exclude_none=True)
    # The pin runs on the report's own source, as drill does, so its values agree.
    return answer(
        intent, report_id, s.context(report.source_id), choice=choice,
        objective=report.spec.objective,
    )
