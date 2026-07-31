"""User-scope tests for the agent-runs API."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _run_demo(client: TestClient, user_id: str) -> dict:
    resp = client.post(
        "/api/v1/agent-runs/manual-jd-analysis-demo",
        json={"jd_text": "Python backend engineer"},
        headers={"X-User-Id": user_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["agent_run"]


def test_agent_runs_are_listed_only_for_current_user(client: TestClient) -> None:
    owner_run = _run_demo(client, "run_owner")
    other_run = _run_demo(client, "run_other")

    owner_list = client.get("/api/v1/agent-runs", headers={"X-User-Id": "run_owner"})
    assert owner_list.status_code == 200
    owner_ids = {item["id"] for item in owner_list.json()["items"]}
    assert owner_run["id"] in owner_ids
    assert other_run["id"] not in owner_ids


def test_agent_run_detail_is_user_scoped(client: TestClient) -> None:
    owner_run = _run_demo(client, "detail_owner")

    owner_view = client.get(
        f"/api/v1/agent-runs/{owner_run['id']}",
        headers={"X-User-Id": "detail_owner"},
    )
    assert owner_view.status_code == 200

    intruder_view = client.get(
        f"/api/v1/agent-runs/{owner_run['id']}",
        headers={"X-User-Id": "detail_intruder"},
    )
    assert intruder_view.status_code == 404
