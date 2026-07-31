"""Smoke test for the health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    # Tolerate DB/Redis being down in the test environment; the endpoint
    # should still respond with 200 and a status field.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "job-search-agent"
