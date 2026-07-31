"""FastAPI dependencies shared across routers."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.cache.redis import RedisClient, get_redis_client
from app.db.session import get_db
from app.models_gateway.base import ModelGateway
from app.models_gateway.factory import get_model_gateway


def get_db_session() -> Session:  # type: ignore[misc]
    yield from get_db()


def get_redis_dep() -> RedisClient:
    return get_redis_client()


def get_model_gateway_dep() -> ModelGateway:
    return get_model_gateway()
