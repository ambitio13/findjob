"""BOSS Web platform adapter package.

The real adapter lives in :mod:`app.platforms.boss.adapter` and is enabled only
behind an explicit environment flag (``BOSS_ADAPTER_ENABLED``). Tests use the
fake adapter in :mod:`app.platforms.boss.fake_adapter` for deterministic
behavior.
"""
