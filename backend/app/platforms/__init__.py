"""Platform adapter package.

Each platform (BOSS, Lagou, ...) gets its own adapter module under a typed
boundary defined in :mod:`app.platforms.base`. Product services must call
adapters through the :class:`~app.platforms.base.PlatformAdapter` protocol and
must never contain selectors, DOM traversal, or browser-specific retry logic
(see ``.trellis/spec/big-question/platform-integration-strategy.md``).

Platform resolution
-------------------

:func:`get_adapter_for_platform` is the single entry point product services
use to resolve an adapter for an application's platform:

- ``"boss"`` → the BOSS adapter family (userscript bridge / real / fake,
  selected by environment flags via :func:`app.platforms.boss.registry.get_adapter`).
- anything else (``"manual"``, lagou, zhipin, ...) → the first-class
  :class:`~app.platforms.manual.adapter.ManualPlatformAdapter` (generate →
  copy → user pastes). Manual is a designed delivery path, not a degraded
  fallback: the product must stay fully useful whenever automation is
  unavailable.
"""

from __future__ import annotations

from app.platforms.base import PlatformAdapter
from app.platforms.manual.adapter import ManualPlatformAdapter


def get_adapter_for_platform(
    platform: str, *, scenario: str | None = None
) -> PlatformAdapter:
    """Resolve the adapter for ``platform``.

    ``scenario`` is only meaningful for the BOSS fake adapter (test
    determinism) and is ignored everywhere else.
    """
    if platform == "boss":
        from app.platforms.boss.registry import get_adapter

        return get_adapter(scenario=scenario)
    return ManualPlatformAdapter()


__all__ = ["ManualPlatformAdapter", "PlatformAdapter", "get_adapter_for_platform"]
