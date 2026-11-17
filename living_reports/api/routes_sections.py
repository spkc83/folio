"""Section routes: edit a section's query, chart, words, and blocks, and record feedback."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from living_reports.api.deps import Stores, stores
from living_reports.engine import OutOfScope, Query, QueryRejected
from living_reports.engine.query import flat_filters, stored_query
from living_reports.harness.builder import INVALID, UNAVAILABLE, AskContext, _cost
from living_reports.harness.commands import (
    CommandResult,
    Signal,
    SpecRejected,
    can_edit,
    chart_forms,
    edit_prose,
    feedback_of,
    record_feedback,
    ref_for,
    remove_block,
    remove_section,
    reorder_blocks,
    rerun_section,
    rewrite_section,
    section_index,
    swap_chart,
)
from living_reports.harness.llm import ModelUnavailable
from living_reports.reports.store import StoredReport

router = APIRouter(prefix="/api/reports/{report_id}/sections/{section_id}")

NO_SECTION = "No such section"
EXPLORATORY = "An exploratory report has no query to edit."
NO_RIGHTS = "Only an author of this report can change its words."


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChartBody(_Body):
    form: str


class FeedbackBody(_Body):
    signal: Signal


class ProseBody(_Body):
    # A person's words, bounded so one request cannot fill the store.
    paragraphs: list[str] = Field(max_length=50)

    @model_validator(mode="after")
    def _short(self) -> "ProseBody":
        if any(len(p) > 4000 for p in self.paragraphs):
            raise ValueError("A paragraph has at most 4,000 characters.")
        return self


class BlocksBody(_Body):
    order: list[int] | None = None
    remove: int | None = None

    @model_validator(mode="after")
    def _one(self) -> "BlocksBody":
        if (self.order is None) == (self.remove is None):
            raise ValueError("Give exactly one of order and remove.")
        return self


class CommandBody(_Body):
    command: Literal["shorter", "expand", "regenerate", "remove"]


def _report(report_id: str, section_id: str, s: Stores, query: bool = True) -> StoredReport:
    report = s.reports.get(report_id)
    try:
        if report is None:
            raise KeyError(report_id)
        section_index(report, section_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=NO_SECTION) from None
    if query and report.tier == "exploratory":
        raise HTTPException(status_code=409, detail=EXPLORATORY)
    return report


def _context(report: StoredReport, s: Stores) -> AskContext:
    # A section changes on the data of its report, as a pin and a drill do.
    return AskContext(store=s.store, reports=s.reports, source_id=report.source_id, user=s.user)


MAX_VALUES, MAX_FILTERS, MAX_DIMENSIONS = 100, 20, 5


def _bounded(query: Query) -> None:
    """A chip edit comes from a person's page: refuse lists that no chip can make."""
    filters = flat_filters(query.filters)
    problem = (
        "A filter has at most 100 values." if any(len(f.values) > MAX_VALUES for f in filters)
        else "A query has at most 20 filters." if len(filters) > MAX_FILTERS
        else "A query has at most 5 dimensions." if len(query.dimensions) > MAX_DIMENSIONS
        else None
    )
    if problem:
        raise HTTPException(status_code=422, detail=problem)


@router.post("/query", response_model=CommandResult)
def edit_query(
    report_id: str, section_id: str, body: Query, s: Stores = Depends(stores)
) -> CommandResult:
    _bounded(body)
    report = _report(report_id, section_id, s)
    try:
        _, dropped, stale = rerun_section(report, section_id, body, _context(report, s))
    except OutOfScope as error:
        raise HTTPException(status_code=422, detail=error.reason) from None
    except QueryRejected as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except KeyError:
        raise HTTPException(status_code=404, detail=NO_SECTION) from None
    return CommandResult(kind="ok", wording_dropped=dropped, wording_stale=stale)


def _editor(report: StoredReport, s: Stores) -> None:
    if not can_edit(s.user, report):
        raise HTTPException(status_code=403, detail=NO_RIGHTS)


@router.get("/forms")
def forms(report_id: str, section_id: str, s: Stores = Depends(stores)) -> dict:
    report = _report(report_id, section_id, s)
    ref = ref_for(section_id)
    if ref not in report.query_defs:
        return {"forms": []}
    query = stored_query(report.query_defs[ref], s.graph.model())
    return {"forms": chart_forms(query, report.queries, ref)}


@router.post("/chart", response_model=CommandResult)
def chart(
    report_id: str, section_id: str, body: ChartBody, s: Stores = Depends(stores)
) -> CommandResult:
    report = _report(report_id, section_id, s)
    try:
        swap_chart(report, section_id, body.form, _context(report, s))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return CommandResult(kind="ok")


@router.post("/command", response_model=CommandResult)
def command(
    report_id: str, section_id: str, body: CommandBody, s: Stores = Depends(stores)
) -> CommandResult:
    report = _report(report_id, section_id, s)
    _editor(report, s)
    ctx = _context(report, s)
    if body.command == "remove":
        try:
            remove_section(report, section_id, ctx)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return CommandResult(kind="ok")
    try:
        saved = rewrite_section(report, section_id, body.command, ctx)
    except ModelUnavailable:
        return CommandResult(kind="failed", message=UNAVAILABLE, cost=_cost(ctx))
    if saved is None:
        return CommandResult(kind="failed", message=INVALID, cost=_cost(ctx))
    return CommandResult(kind="ok", cost=_cost(ctx))


@router.post("/feedback")
def post_feedback(
    report_id: str, section_id: str, body: FeedbackBody, s: Stores = Depends(stores)
) -> dict:
    _report(report_id, section_id, s, query=False)
    record_feedback(s.store, report_id, section_id, s.user, body.signal)
    return {"ok": True}


@router.get("/feedback")
def get_feedback(report_id: str, section_id: str, s: Stores = Depends(stores)) -> dict:
    _report(report_id, section_id, s, query=False)
    return {"signal": feedback_of(s.store, report_id, section_id, s.user)}


@router.put("/prose", response_model=CommandResult)
def put_prose(report_id: str, section_id: str, body: ProseBody, s: Stores = Depends(stores)):
    report = _report(report_id, section_id, s, query=False)
    _editor(report, s)
    try:
        edit_prose(report, section_id, body.paragraphs, _context(report, s), s.user)
    except SpecRejected as error:
        first = error.issues[0]
        detail = first.message if first.code == "empty" else f"{first.code}: {first.message}"
        issues = [i.model_dump() for i in error.issues]
        return JSONResponse({"detail": detail, "issues": issues}, status_code=422)
    return CommandResult(kind="ok")


@router.post("/blocks", response_model=CommandResult)
def post_blocks(
    report_id: str, section_id: str, body: BlocksBody, s: Stores = Depends(stores)
) -> CommandResult:
    report = _report(report_id, section_id, s, query=False)
    _editor(report, s)
    ctx = _context(report, s)
    try:
        if body.order is not None:
            reorder_blocks(report, section_id, body.order, ctx)
        else:
            remove_block(report, section_id, body.remove, ctx)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return CommandResult(kind="ok")
