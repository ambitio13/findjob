"""Config assertion tests for the reverse-proxy real-IP trust chain.

ProxyHeadersMiddleware lives in uvicorn's server layer, so its XFF parsing
cannot be exercised through ``TestClient``. Instead these tests assert the
*configuration* that makes the trust chain work:

- backend compose command enables ``--proxy-headers`` with a non-``*``
  ``--forwarded-allow-ips``;
- the frontend container has the fixed IP that ``FORWARDED_ALLOW_IPS``
  points at in prod;
- nginx appends to (not replaces) ``X-Forwarded-For``;
- ``.env.example`` documents the variable and forbids ``*``.

``docker compose`` and the project's ``.env`` files are parsed here (not by a
running server) so the tests are network-free and fast.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_NGINX = _REPO_ROOT / "frontend" / "nginx.conf"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"


def _load_compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text())


def test_backend_command_enables_proxy_headers() -> None:
    """The backend compose command must pass ``--proxy-headers``."""
    backend = _load_compose()["services"]["backend"]
    command = backend.get("command", "")
    # ``command`` may be a multi-line string in compose.
    assert "--proxy-headers" in command, (
        "backend command must include --proxy-headers so uvicorn trusts "
        "X-Forwarded-For from the reverse proxy"
    )


def test_backend_command_forwarded_allow_ips_not_wildcard() -> None:
    """``--forwarded-allow-ips`` must never be ``*``."""
    backend = _load_compose()["services"]["backend"]
    command = backend.get("command", "")
    # The allow-ips value is injected from env with a safe default.
    assert "forwarded-allow-ips=" in command, (
        "backend command must set --forwarded-allow-ips explicitly"
    )
    # The literal '*' must never appear as the resolved value. The compose uses
    # a shell variable expansion, so we check the default fallback is not '*'.
    match = re.search(r"forwarded-allow-ips=\$\$\{[^}]*:-(.*?)\}", command)
    assert match, "expected --forwarded-allow-ips with a default value"
    default_value = match.group(1)
    assert default_value != "*", (
        "FORWARDED_ALLOW_IPS default must never be '*' — a wildcard lets any "
        "client forge its IP and bypass all per-IP rate limiting"
    )


def test_backend_environment_has_forwarded_allow_ips() -> None:
    """The backend service must inject ``FORWARDED_ALLOW_IPS`` from env."""
    backend = _load_compose()["services"]["backend"]
    env = backend.get("environment", {})
    assert "FORWARDED_ALLOW_IPS" in env, "backend environment must pass FORWARDED_ALLOW_IPS through"
    default = env["FORWARDED_ALLOW_IPS"]
    # Default in compose is 127.0.0.1 (safe for local dev).
    assert default != "*", "FORWARDED_ALLOW_IPS compose default must not be '*'"


def test_frontend_has_fixed_ip() -> None:
    """The frontend (nginx) container must have a static IP for uvicorn to trust."""
    frontend = _load_compose()["services"]["frontend"]
    networks = frontend.get("networks", {})
    assert "appnet" in networks, "frontend must be on the appnet network"
    ip = networks["appnet"].get("ipv4_address") if isinstance(networks["appnet"], dict) else None
    assert ip, (
        "frontend must have a fixed ipv4_address on appnet — uvicorn "
        "--forwarded-allow-ips points at this exact IP"
    )


def test_appnet_subnet_defined() -> None:
    """A dedicated appnet subnet must exist so the frontend static IP is valid."""
    networks = _load_compose().get("networks", {})
    assert "appnet" in networks, "appnet network must be defined"
    configs = networks["appnet"].get("ipam", {}).get("config", [])
    assert configs, "appnet must have an ipam subnet config"
    subnet = configs[0].get("subnet")
    assert subnet, "appnet must specify a subnet"


def test_all_services_on_appnet() -> None:
    """Once appnet is declared, all services must join it (or DNS breaks)."""
    services = _load_compose()["services"]
    for name in ("postgres", "redis", "backend", "worker", "frontend"):
        nets = services[name].get("networks")
        assert nets, f"{name} must be on appnet — declaring networks drops the default network"


def test_nginx_uses_proxy_add_x_forwarded_for() -> None:
    """nginx must append to XFF, not replace it."""
    nginx_conf = _NGINX.read_text()
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for" in nginx_conf, (
        "nginx must use $proxy_add_x_forwarded_for so the client's real IP is "
        "appended while forged client-side XFF entries are preserved (and "
        "ignored by uvicorn's right-to-left trust scan)"
    )
    assert "proxy_set_header X-Real-IP $remote_addr" in nginx_conf, (
        "nginx must set X-Real-IP from $remote_addr (not from a client header)"
    )


def test_env_example_documents_forwarded_allow_ips() -> None:
    """``.env.example`` must document ``FORWARDED_ALLOW_IPS`` and forbid ``*``."""
    content = _ENV_EXAMPLE.read_text()
    assert "FORWARDED_ALLOW_IPS" in content, ".env.example must document FORWARDED_ALLOW_IPS"
    assert "NEVER" in content or "never" in content, (
        ".env.example must explicitly warn against using '*'"
    )
