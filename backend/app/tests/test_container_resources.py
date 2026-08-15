"""Docker compose resource limit and healthcheck tests (08-15-runtime-guardrails, D2).

Validates that docker-compose.yml has:
- deploy.resources.limits for backend and worker;
- healthcheck for backend;
- frontend depends_on backend with condition: service_healthy.

These are config-assertion tests (no container runtime required).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture()
def compose_services() -> dict:
    """Load docker-compose.yml and return the services dict."""
    compose_path = Path(__file__).resolve().parents[3] / "docker-compose.yml"
    with open(compose_path) as f:
        data = yaml.safe_load(f)
    return data["services"]


def test_backend_has_resource_limits(compose_services: dict) -> None:
    """Backend must have deploy.resources.limits (memory + cpu)."""
    backend = compose_services["backend"]
    limits = backend.get("deploy", {}).get("resources", {}).get("limits", {})
    assert "memory" in limits, "backend must have a memory limit"
    assert "cpus" in limits, "backend must have a cpu limit"


def test_worker_has_resource_limits(compose_services: dict) -> None:
    """Worker must have deploy.resources.limits (memory + cpu)."""
    worker = compose_services["worker"]
    limits = worker.get("deploy", {}).get("resources", {}).get("limits", {})
    assert "memory" in limits, "worker must have a memory limit"
    assert "cpus" in limits, "worker must have a cpu limit"


def test_worker_memory_higher_than_backend(compose_services: dict) -> None:
    """Worker carries parsing + LLM work, so it should get more memory."""
    backend_mem = compose_services["backend"]["deploy"]["resources"]["limits"]["memory"]
    worker_mem = compose_services["worker"]["deploy"]["resources"]["limits"]["memory"]

    def _to_mb(val: str) -> int:
        if val.endswith("g"):
            return int(val[:-1]) * 1024
        if val.endswith("m"):
            return int(val[:-1])
        return int(val)

    assert _to_mb(worker_mem) > _to_mb(backend_mem)


def test_backend_has_healthcheck(compose_services: dict) -> None:
    """Backend must have a healthcheck pointing at the health endpoint."""
    backend = compose_services["backend"]
    healthcheck = backend.get("healthcheck")
    assert healthcheck is not None, "backend must have a healthcheck"
    test_cmd = healthcheck.get("test", [])
    # The test command should reference the health endpoint.
    cmd_str = " ".join(str(part) for part in test_cmd)
    assert "/api/v1/health" in cmd_str, "healthcheck must hit /api/v1/health"


def test_frontend_depends_on_backend_healthy(compose_services: dict) -> None:
    """Frontend should wait for backend to be healthy before starting."""
    frontend = compose_services["frontend"]
    depends_on = frontend.get("depends_on", {})
    backend_dep = depends_on.get("backend", {})
    if isinstance(backend_dep, dict):
        assert backend_dep.get("condition") == "service_healthy", (
            "frontend should depend on backend with condition: service_healthy"
        )


def test_compose_config_valid() -> None:
    """docker compose config should validate without errors."""
    compose_path = Path(__file__).resolve().parents[3] / "docker-compose.yml"
    # Just verify the YAML parses cleanly (already done by the fixture, but
    # this is an explicit assertion for CI).
    with open(compose_path) as f:
        data = yaml.safe_load(f)
    assert "services" in data
    assert "backend" in data["services"]
    assert "worker" in data["services"]
