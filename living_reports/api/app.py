"""FastAPI application. Run: uvicorn living_reports.api.app:create_app --factory --port 8100"""
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from living_reports.api import (
    routes_ask,
    routes_changes,
    routes_editions,
    routes_explore,
    routes_graph,
    routes_home,
    routes_model,
    routes_reports,
    routes_schema,
    routes_sections,
    routes_signoff,
    routes_threads,
    routes_vocabulary,
)
from living_reports.config import get_settings
from living_reports.demos import get_demo
from living_reports.fixtures import fraud_overview
from living_reports.harness import decide
from living_reports.reports.signoff import Conflict, Forbidden
from living_reports.reports.store import StaleReport
from living_reports.sources import SourceError
from living_reports.sources.build import build_demo_source, demo_available, demo_moment
from living_reports.sources.registry import source_for, sources
from living_reports.store import get_store

log = logging.getLogger(__name__)

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'"
)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Start loading the decision models now: a cold load takes longer than a decision
    # request may wait. This runs in the background and does not delay the start.
    decide.warm()
    store = get_store()
    # The demo model and its source come together. Without the demo source, the model
    # comes from discovery and confirmation (the model page).
    if demo_available():
        get_demo().seed(store.graph)
        # Build the demo source before the first request, at the moment it last reached.
        try:
            await asyncio.to_thread(build_demo_source, demo_moment(store) or 1)
        except (SourceError, OSError) as error:
            log.error("The demo source cannot be built: %s", error)
    for source_id, reason in (await asyncio.to_thread(_source_health)).items():
        if reason != "ok":
            log.error("source %s unavailable: %s", source_id, reason)
    yield


def _source_health() -> dict[str, str]:
    """``ok`` or a short reason for each configured source. A reason holds no DSN or path."""
    health = {}
    for source_id in sources():
        try:
            source_for(source_id).check()
            health[source_id] = "ok"
        except SourceError as error:
            health[source_id] = str(error)
    return health


def create_app() -> FastAPI:
    app = FastAPI(title="Folio", lifespan=_lifespan)
    app.include_router(routes_reports.router)
    app.include_router(routes_ask.router)
    app.include_router(routes_threads.router)
    app.include_router(routes_editions.router)
    app.include_router(routes_signoff.router)
    app.include_router(routes_graph.router)
    app.include_router(routes_changes.router)
    app.include_router(routes_explore.router)
    app.include_router(routes_home.router)
    app.include_router(routes_model.router)
    app.include_router(routes_vocabulary.router)
    app.include_router(routes_schema.router)
    app.include_router(routes_sections.router)

    @app.exception_handler(Forbidden)
    async def forbidden(_: Request, error: Forbidden) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=403)

    @app.exception_handler(StaleReport)
    async def stale(_: Request, __: StaleReport) -> JSONResponse:
        body = {"detail": "This report changed while you worked. Open it again."}
        return JSONResponse(body, status_code=409)

    @app.exception_handler(Conflict)
    async def conflict(_: Request, error: Conflict) -> JSONResponse:
        body = {"detail": error.detail, "current_edition": error.current_edition}
        return JSONResponse(body, status_code=409)

    @app.middleware("http")
    async def policy(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        return response

    @app.get("/api/health")
    def health() -> JSONResponse:
        store_state = "ok"
        try:
            store = get_store()
            store.row("SELECT 1")
            store.graph.version()
        except Exception:  # noqa: BLE001 - any store failure is a degraded health state
            log.exception("The health check could not read the store")
            store_state = "unavailable"
        source_states = _source_health()
        ok = store_state == "ok" and all(v == "ok" for v in source_states.values())
        body = {"status": "ok" if ok else "degraded", "store": store_state,
                "sources": source_states}
        return JSONResponse(body, status_code=200 if ok else 503)

    def build_fixture() -> fraud_overview.BuiltReport:
        return fraud_overview.build(get_store())

    app.state.build_fixture = build_fixture
    return app
