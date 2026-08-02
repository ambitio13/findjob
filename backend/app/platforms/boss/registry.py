"""BOSS Web adapter selection.

The real Playwright-backed BOSS adapter is enabled only behind the
``BOSS_ADAPTER_ENABLED`` environment flag (design.md §Browser Automation
Choice). When the flag is unset (the default, including all tests and local
dev), :func:`get_adapter` returns the fake adapter so product services and
tests stay deterministic without a browser.

The real adapter module (:mod:`app.platforms.boss.adapter`) imports Playwright
lazily inside ``__init__`` so the dependency is only required when the flag is
set. This keeps the default install lightweight.
"""

from __future__ import annotations

import os

from app.core.logging import get_logger
from app.platforms.base import PlatformAdapter
from app.platforms.boss.fake_adapter import FakeBossAdapter

_log = get_logger("app.platforms.boss.registry")

#: Environment flag that opts into the real Playwright-backed BOSS adapter.
REAL_ADAPTER_FLAG = "BOSS_ADAPTER_ENABLED"


def get_adapter(*, scenario: str | None = None) -> PlatformAdapter:
    """Return the active BOSS adapter.

    When ``BOSS_ADAPTER_ENABLED`` is set to a truthy value the real Playwright
    adapter is constructed; otherwise the fake adapter is returned. ``scenario``
    is forwarded to the fake adapter for test determinism and is ignored by the
    real adapter.
    """
    if _flag_enabled():
        _log.info("boss.adapter.real_enabled")
        from app.platforms.boss.adapter import RealBossAdapter  # lazy Playwright import

        return RealBossAdapter()
    _log.info("boss.adapter.fake_enabled")
    return FakeBossAdapter(scenario=scenario or "filled_preview")


def _flag_enabled() -> bool:
    value = os.environ.get(REAL_ADAPTER_FLAG, "").strip().lower()
    return value in {"1", "true", "yes", "on"}
