"""Smoke test for the health endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    # Tolerate DB/Redis being down in the test environment; the endpoint
    # should still respond with 200 and a status field.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "job-search-agent"


def test_health_includes_model_budget_tripped_field(client: TestClient) -> None:
    """The health response must include the model_budget_tripped field (AC1)."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "model_budget_tripped" in body
    assert isinstance(body["model_budget_tripped"], bool)


def test_health_reports_tripped_when_budget_exceeded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the budget is tripped, health should report model_budget_tripped=true."""
    from app.models_gateway import budget

    monkeypatch.setattr(budget, "is_tripped_today", lambda **kw: True)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["model_budget_tripped"] is True


def test_health_reports_not_tripped_by_default(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a tripped marker, health should report model_budget_tripped=false."""
    from app.models_gateway import budget

    monkeypatch.setattr(budget, "is_tripped_today", lambda **kw: False)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["model_budget_tripped"] is False
