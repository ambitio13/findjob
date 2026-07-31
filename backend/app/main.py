"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging()
    log = get_logger("app.main")
    log.info("app.starting", app=get_settings().app_name, env=get_settings().app_env)
    yield
    log.info("app.stopping", app=get_settings().app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Job Search Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
