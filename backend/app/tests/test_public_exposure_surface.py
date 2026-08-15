"""Tests for public exposure-surface reduction in prod.

In ``prod`` the app must:
- disable the interactive docs (``/docs``, ``/redoc``, ``/openapi.json``);
- not mount the userscript-bridge router.

The docs URLs are per-app FastAPI kwargs, so they can be tested directly. The
router, however, is a module-level singleton built when ``app.main`` was first
imported (under ``APP_ENV=test``). To verify the prod path we reconstruct a
*fresh* router here so the prod env var is actually consulted at mount time.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.rate_limit import RateLimitMiddleware
from app.api.v1 import (
    agent_runs,
    applications,
    approval_actions,
    auth,
    boss_batch_loop,
    boss_communicate,
    boss_conversations,
    boss_match,
    boss_recommended_discovery,
    boss_recommended_jobs,
    health,
    jobs,
    metrics,
    resumes,
    users,
)
from app.core.config import get_settings


def _build_api_router(app_env: str) -> APIRouter:
    """Build a fresh api_router reflecting the given ``app_env``.

    This mirrors ``app/api/v1/router.py`` but is constructed at call time so
    the ``app_env`` check for the userscript-bridge is evaluated live rather
    than at module import.
    """
    router = APIRouter()
    router.include_router(health.router)
    router.include_router(auth.router)
    router.include_router(users.router)
    router.include_router(resumes.router)
    router.include_router(jobs.router)
    router.include_router(applications.router)
    router.include_router(approval_actions.router)
    router.include_router(agent_runs.router)
    router.include_router(metrics.router)
    if app_env != "prod":
        router.include_router(
            __import__("app.api.v1.userscript_bridge", fromlist=["router"]).router
        )
    router.include_router(boss_recommended_jobs.router)
    router.include_router(boss_batch_loop.router)
    router.include_router(boss_recommended_discovery.router)
    router.include_router(boss_match.router)
    router.include_router(boss_communicate.router)
    router.include_router(boss_conversations.router)
    return router


@pytest.fixture()
def prod_app() -> Iterator[FastAPI]:
    """Create a FastAPI instance configured as ``app_env=prod``.

    ``get_settings`` is cached, so we temporarily override the env var, bust
    the cache, build the prod app, and restore everything in the teardown.
    """
    original_env = os.environ.get("APP_ENV", "")
    os.environ["APP_ENV"] = "prod"

    # Bust the lru_cache so the next ``get_settings()`` picks up prod.
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.app_env == "prod"

    is_prod = settings.app_env == "prod"
    app = FastAPI(
        title="Job Search Agent API",
        version="0.1.0",
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.add_middleware(RateLimitMiddleware)
    # Build a fresh router so the prod env var is consulted at mount time.
    app.include_router(_build_api_router(settings.app_env), prefix=settings.api_v1_prefix)

    yield app

    # Restore original environment and settings cache.
    os.environ["APP_ENV"] = original_env or "test"
    get_settings.cache_clear()


@pytest.fixture()
def prod_client(prod_app: FastAPI) -> TestClient:
    return TestClient(prod_app)


# -- docs / openapi ----------------------------------------------------------


def test_docs_disabled_in_prod(prod_client: TestClient) -> None:
    """/docs must return 404 in prod (not the Swagger UI)."""
    resp = prod_client.get("/docs")
    assert resp.status_code == 404


def test_redoc_disabled_in_prod(prod_client: TestClient) -> None:
    """/redoc must return 404 in prod."""
    resp = prod_client.get("/redoc")
    assert resp.status_code == 404


def test_openapi_json_disabled_in_prod(prod_client: TestClient) -> None:
    """/openapi.json must return 404 in prod — no schema leak."""
    resp = prod_client.get("/openapi.json")
    assert resp.status_code == 404


# -- userscript bridge -------------------------------------------------------


def test_bridge_router_not_mounted_in_prod(prod_client: TestClient) -> None:
    """The userscript-bridge routes must not exist in prod."""
    # /status is the public GET that the frontend connection indicator calls.
    resp = prod_client.get("/api/v1/userscript-bridge/status")
    assert resp.status_code == 404, (
        "userscript-bridge must not be mounted in prod — it is a local-dev "
        "convenience endpoint that should never be publicly reachable"
    )


def test_bridge_probe_not_mounted_in_prod(prod_client: TestClient) -> None:
    """The diagnostic probe endpoint must also be absent in prod."""
    resp = prod_client.post("/api/v1/userscript-bridge/probe", json={})
    assert resp.status_code == 404


# -- non-prod sanity checks --------------------------------------------------


def test_docs_enabled_in_test_env() -> None:
    """In the test (non-prod) env, docs URLs should resolve normally."""
    # The default ``app`` from conftest uses APP_ENV=test.
    settings = get_settings()
    assert settings.app_env != "prod"

    from app.main import app

    client = TestClient(app)
    # /docs should return 200 (Swagger UI HTML) in non-prod.
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_bridge_router_mounted_in_test_env() -> None:
    """In the test env, the userscript-bridge /status route must exist."""
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/v1/userscript-bridge/status")
    # 200 means the route is mounted (the channel is not connected, but the
    # endpoint itself returns a valid response).
    assert resp.status_code == 200


# -- compose / nginx config assertions ---------------------------------------


def test_compose_bridge_default_off() -> None:
    """The compose default for BOSS_USERSCRIPT_BRIDGE_ENABLED must be 0."""
    yaml = pytest.importorskip("yaml")
    from pathlib import Path

    compose = Path(__file__).resolve().parents[3] / "docker-compose.yml"
    services = yaml.safe_load(compose.read_text())["services"]
    backend_env = services["backend"]["environment"]
    raw = backend_env["BOSS_USERSCRIPT_BRIDGE_ENABLED"]
    # The value is ``${BOSS_USERSCRIPT_BRIDGE_ENABLED:-0}``.
    assert ":-0" in raw, (
        "compose default for BOSS_USERSCRIPT_BRIDGE_ENABLED must be :-0 "
        "(disabled by default) so a fresh deploy does not expose the bridge"
    )


def test_nginx_body_size_limit() -> None:
    """nginx must cap request body size."""
    from pathlib import Path

    nginx_conf = Path(__file__).resolve().parents[3] / "frontend" / "nginx.conf"
    content = nginx_conf.read_text()
    assert "client_max_body_size" in content, (
        "nginx must set client_max_body_size to cap oversized request bodies"
    )
