"""FastAPI dependencies shared across routers."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.cache.redis import RedisClient, get_redis_client
from app.core.config import get_settings
from app.core.context import clear_current_user_id, set_current_user_id
from app.db.models.models import UserProfile
from app.db.repositories import user_profile_repo
from app.db.session import get_db
from app.models_gateway.base import ModelGateway
from app.models_gateway.factory import get_model_gateway


def get_db_session() -> Session:  # type: ignore[misc]
    yield from get_db()


def get_redis_dep() -> RedisClient:
    return get_redis_client()


def get_model_gateway_dep() -> ModelGateway:
    return get_model_gateway()


def get_current_user(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    db: Session = Depends(get_db_session),
) -> Generator[UserProfile, None, None]:
    """Resolve the current user from the ``X-User-Id`` header (or demo user).

    Ensures a profile row exists for the user id, sets the request-scoped
    ``ContextVar`` mirror, yields the ``UserProfile`` row, and resets the
    ContextVar in ``finally`` so user context never leaks across requests.
    """
    settings = get_settings()
    user_id = (x_user_id or "").strip() or settings.demo_user_id
    user = user_profile_repo.ensure_default(db, user_id)
    set_current_user_id(user.id)
    try:
        yield user
    finally:
        clear_current_user_id()
