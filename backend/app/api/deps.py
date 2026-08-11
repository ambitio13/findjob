"""FastAPI dependencies shared across routers."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.cache.redis import RedisClient, get_redis_client
from app.core.config import get_settings
from app.core.context import clear_current_user_id, set_current_user_id
from app.core.security import decode_access_token
from app.db.models.models import UserProfile
from app.db.repositories import auth_user_repo, user_profile_repo
from app.db.session import get_db
from app.models_gateway.base import ModelGateway
from app.models_gateway.factory import get_model_gateway


def get_db_session() -> Session:  # type: ignore[misc]
    yield from get_db()


def get_redis_dep() -> RedisClient:
    return get_redis_client()


def get_model_gateway_dep() -> ModelGateway:
    return get_model_gateway()


def _resolve_bearer_user_id(authorization: str | None) -> str | None:
    """Return the auth user id carried by a valid ``Authorization: Bearer``
    token, or ``None`` when the header is absent/invalid.

    The subject is validated against ``auth_users`` so a token for a deleted
    user stops working immediately instead of lingering until expiry.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    subject = decode_access_token(token)
    return subject or None


def get_current_user(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Session = Depends(get_db_session),
) -> Generator[UserProfile, None, None]:
    """Resolve the current user from a bearer token (or legacy dev fallback).

    Resolution order:

    1. ``Authorization: Bearer <token>`` — the signed token's subject is
       validated against ``auth_users``; the request is attributed to the
       profile row sharing that id.
    2. Non-prod fallback — the legacy ``X-User-Id`` header, or the demo user
       when absent. In ``prod`` this fallback is disabled and a missing or
       invalid token yields ``401``.

    Ensures a profile row exists for the resolved id, sets the request-scoped
    ``ContextVar`` mirror, yields the ``UserProfile`` row, and resets the
    ContextVar in ``finally`` so user context never leaks across requests.
    """
    settings = get_settings()
    user_id: str | None = None

    token_subject = _resolve_bearer_user_id(authorization)
    if token_subject is not None:
        if auth_user_repo.get(db, token_subject) is not None:
            user_id = token_subject
        # A token whose user no longer exists falls through to the
        # environment rules below (401 in prod).

    if user_id is None:
        if settings.app_env == "prod":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"reason": "authentication_required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id = (x_user_id or "").strip() or settings.demo_user_id

    user = user_profile_repo.ensure_default(db, user_id)
    set_current_user_id(user.id)
    try:
        yield user
    finally:
        clear_current_user_id()
