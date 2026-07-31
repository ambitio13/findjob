"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.deps import get_db_session, get_redis_dep
from app.cache.redis import RedisClient
from app.core.config import get_settings
from app.schemas.api import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(
    db=Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_dep),
) -> HealthResponse:
    settings = get_settings()
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {type(exc).__name__}"

    redis_status = "ok" if redis.ping() else "error"

    return HealthResponse(
        app=settings.app_name,
        env=settings.app_env,
        db=db_status,
        redis=redis_status,
    )
