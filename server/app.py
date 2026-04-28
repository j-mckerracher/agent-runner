"""FastAPI application factory for agent-runner local server."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.routes.runs import router as runs_router, queue_processor
from server.routes.agents import router as agents_router
from server.routes.corpus import router as corpus_router
from server.routes.evaluate import router as evaluate_router
from server.routes.settings import router as settings_router

_GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data dir exists
    from server.config import _data_dir
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "cassettes").mkdir(exist_ok=True)
    (data_dir / "memory").mkdir(exist_ok=True)

    # Initialize DB
    from server.db import get_conn
    get_conn()

    # Create the job queue and start the queue processor
    app.state.job_queue = asyncio.Queue()
    processor_task = asyncio.create_task(queue_processor(app))

    yield

    # Cleanup
    processor_task.cancel()
    try:
        await processor_task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="agent-runner",
        version=_VERSION,
        lifespan=lifespan,
    )

    # CORS: only allow localhost origins (GUI served from same origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(runs_router)
    app.include_router(agents_router)
    app.include_router(corpus_router)
    app.include_router(evaluate_router)
    app.include_router(settings_router)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": _VERSION}

    @app.get("/", response_class=HTMLResponse)
    def serve_gui():
        index = _GUI_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index), media_type="text/html")
        return HTMLResponse("<h1>GUI not found</h1><p>gui/index.html missing.</p>", status_code=404)

    return app
