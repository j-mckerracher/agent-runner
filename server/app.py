"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .events import EventBus
from .jobs import manager
from .paths import GUI_ROOT
from .routes import agents as agents_routes
from .routes import corpus as corpus_routes
from .routes import evaluate as evaluate_routes
from .routes import runs as runs_routes
from .routes import settings as settings_routes

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    logger.debug("create_app: initialising EventBus and JobManager")
    bus = EventBus()
    mgr = manager(bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Lifespan: starting JobManager (version=%s)", __version__)
        await mgr.start()
        logger.info("Lifespan: JobManager started")
        try:
            yield
        finally:
            logger.info("Lifespan: shutting down JobManager")
            await mgr.shutdown()
            logger.info("Lifespan: JobManager shutdown complete")

    app = FastAPI(title="agent-runner", version=__version__, lifespan=lifespan)
    app.state.bus = bus
    app.state.manager = mgr
    logger.debug("FastAPI app created (title=%s, version=%s)", app.title, __version__)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.debug("CORS middleware registered")

    @app.middleware("http")
    async def _request_logger(request: Request, call_next) -> Response:
        start = time.perf_counter()
        logger.info("→ %s %s", request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "✗ %s %s raised after %dms: %s",
                request.method, request.url.path, elapsed_ms, exc,
            )
            raise
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "← %s %s  status=%d  %dms",
            request.method, request.url.path, response.status_code, elapsed_ms,
        )
        return response

    @app.get("/health")
    async def health():
        logger.debug("health check requested")
        return {"status": "ok", "version": __version__}

    app.include_router(runs_routes.router)
    app.include_router(agents_routes.router)
    app.include_router(corpus_routes.router)
    app.include_router(evaluate_routes.router)
    app.include_router(settings_routes.router)
    logger.debug("All routers included")

    @app.get("/")
    async def index():
        index_html = GUI_ROOT / "index.html"
        logger.debug("index route: GUI_ROOT=%s, index_html exists=%s", GUI_ROOT, index_html.exists())
        if index_html.exists():
            return FileResponse(index_html)
        logger.warning("index route: GUI not installed (GUI_ROOT=%s)", GUI_ROOT)
        return JSONResponse({"detail": "GUI not installed"}, status_code=404)

    if GUI_ROOT.is_dir():
        app.mount("/static", StaticFiles(directory=str(GUI_ROOT)), name="static")
        logger.debug("Static files mounted from %s", GUI_ROOT)
    else:
        logger.debug("GUI_ROOT not a directory (%s); /static not mounted", GUI_ROOT)

    logger.info("create_app: application factory complete")
    return app


app = create_app()
