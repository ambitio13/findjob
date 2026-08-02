"""BOSS Web adapter selection.

Three adapter implementations sit behind the same
:class:`~app.platforms.base.PlatformAdapter` protocol, selected by environment
flags:

1. **Userscript bridge** (``boss_userscript_bridge_enabled``) — a Tampermonkey
   userscript running in the page's own JS context communicates with the backend
   via an in-memory instruction queue. This sidesteps BOSS CDP-level automation
   detection and is the recommended mode for real BOSS automation.
2. **Real Playwright/CDP** (``boss_adapter_enabled``) — connects to a real
   Chrome instance via CDP. Preserved as a fallback; BOSS detects CDP in
   production.
3. **Fake** (default) — deterministic in-memory adapter for tests and dev.

Selection priority: userscript > real > fake. Only one is active at a time.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.platforms.base import PlatformAdapter
from app.platforms.boss.fake_adapter import FakeBossAdapter

_log = get_logger("app.platforms.boss.registry")


def get_adapter(*, scenario: str | None = None) -> PlatformAdapter:
    """Return the active BOSS adapter.

    Selection priority: userscript bridge > real Playwright/CDP > fake.
    ``scenario`` is forwarded to the fake adapter for test determinism and is
    ignored by the real and userscript adapters.
    """
    if _userscript_enabled():
        _log.info("boss.adapter.userscript_enabled")
        from app.platforms.boss.userscript_adapter import UserscriptBossAdapter

        return UserscriptBossAdapter()
    if _flag_enabled():
        _log.info("boss.adapter.real_enabled")
        from app.platforms.boss.adapter import RealBossAdapter  # lazy Playwright import

        return RealBossAdapter()
    _log.info("boss.adapter.fake_enabled")
    return FakeBossAdapter(scenario=scenario or "filled_preview")


def _userscript_enabled() -> bool:
    return get_settings().boss_userscript_bridge_enabled


def _flag_enabled() -> bool:
    return get_settings().boss_adapter_enabled
