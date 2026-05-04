"""
FastAPI application entrypoint.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import jobs, leads
from app.config import settings

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "leadgen_started",
        env=settings.app_env,
        storage=settings.storage_backend.value,
        queue=settings.queue_backend.value,
        proxy_enabled=settings.proxy_enabled,
        playwright=settings.use_playwright,
    )
    yield
    # shutdown: nothing to clean up for in-memory backend


def create_app() -> FastAPI:
    app = FastAPI(
        title="Self-Hosted Lead Gen Engine",
        description=(
            "Convert a natural-language prompt into structured B2B/B2C leads "
            "by crawling Google SERP, IndiaMART, JustDial, and company websites."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(leads.router)
    app.include_router(jobs.router)

    try:
        app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
    except Exception:
        pass

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        log.error("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
        log_level="info",
    )
