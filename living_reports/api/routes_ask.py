"""Conversation routes."""
import json
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from living_reports.api.deps import Stores, stores
from living_reports.harness import progress
from living_reports.harness.builder import AskContext, AskResult, answer, explore
from living_reports.harness.ladder import run_ladder
from living_reports.harness.selection import Selection
from living_reports.reports.certified import answer_certified, get_certified
from living_reports.spec.models import Objective

router = APIRouter(prefix="/api")


class AskRequest(BaseModel):
    question: str = ""
    report_id: str | None = None
    source_id: str | None = None
    choice: dict | None = None
    objective: Objective | None = None
    certified_id: str | None = None
    # What the reader pointed at: it narrows the routed query in code.
    selection: Selection | None = None
    # The page reads the steps of the answer under this id while it waits.
    progress_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{8,64}$")

    @model_validator(mode="after")
    def _question_or_certified(self) -> "AskRequest":
        if not self.question.strip() and self.certified_id is None:
            raise ValueError("Give a question or a certified question id.")
        return self


def _context(source_id: str | None, s: Stores) -> AskContext:
    return s.context(source_id)


RUNNING = "This question is already being answered. Wait for that answer."


def duplicate_key(body: AskRequest, user: str, source_id: str, kind: str = "ask") -> str:
    selection = body.selection.model_dump(mode="json") if body.selection else None
    extra = json.dumps([kind, body.certified_id, body.choice, selection], sort_keys=True, default=str)
    return progress.duplicate_key(user, source_id, body.report_id, body.question, extra)


def _run(body: AskRequest, s: Stores, kind: str, work: Callable[[AskContext], AskResult]) -> AskResult:
    """Run one answer: refuse a duplicate of a running question, and record its steps."""
    ctx = _context(body.source_id, s)
    if not progress.start(ctx.turn_id, duplicate_key(body, s.user, ctx.source_id, kind)):
        raise HTTPException(status_code=409, detail=RUNNING)
    progress.track(body.progress_id, ctx.turn_id)
    try:
        return work(ctx)
    finally:
        progress.finish(ctx.turn_id)


@router.post("/ask", response_model=AskResult)
def ask(body: AskRequest, s: Stores = Depends(stores)) -> AskResult:
    def work(ctx: AskContext) -> AskResult:
        if body.certified_id is not None:
            cq = get_certified(s.store, body.certified_id)
            if cq is None:
                raise HTTPException(status_code=404, detail="No such certified question")
            return answer_certified(cq, ctx)
        if body.choice is not None:
            return answer(
                body.question, body.report_id, ctx, body.choice, body.objective, selection=body.selection
            )
        return run_ladder(body.question, body.report_id, ctx, body.selection).answer

    return _run(body, s, "ask", work)


@router.post("/explore", response_model=AskResult)
def explore_route(body: AskRequest, s: Stores = Depends(stores)) -> AskResult:
    return _run(body, s, "explore", lambda ctx: explore(body.question, ctx))


@router.get("/progress/{progress_id}", response_model=progress.ProgressView)
def progress_route(progress_id: str, _: Stores = Depends(stores)) -> progress.ProgressView:
    view = progress.snapshot(progress_id)
    if view is None:
        raise HTTPException(status_code=404, detail="No answer is running under this id.")
    return view
