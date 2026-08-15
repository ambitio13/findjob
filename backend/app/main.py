"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.rate_limit import RateLimitMiddleware
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
    is_prod = settings.app_env == "prod"
    app = FastAPI(
        title="Job Search Agent API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )
    # Phase 0 guardrail: per-IP fixed-window limits (auth / model / global).
    # Fails open when Redis is unavailable.
    app.add_middleware(RateLimitMiddleware)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # Map ModelBudgetExceeded to a 429 with Retry-After pointing at UTC midnight
    # (when the daily counter resets). This is the HTTP-path counterpart of the
    # worker-path branch in handlers.py.
    from starlette.responses import JSONResponse

    from app.models_gateway.budget import ModelBudgetExceeded, _seconds_until_utc_midnight

    @app.exception_handler(ModelBudgetExceeded)
    async def _budget_exceeded_handler(  # type: ignore[no-untyped-def]
        request, exc,  # noqa: ARG001 — FastAPI signature
    ) -> JSONResponse:
        retry_after = _seconds_until_utc_midnight()
        return JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "reason": "model_budget_exceeded",
                    "retry_after_seconds": retry_after,
                }
            },
            headers={"Retry-After": str(retry_after)},
        )

    return app


app = create_app()
