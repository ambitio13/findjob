"""Platform adapter package.

Each platform (BOSS, Lagou, ...) gets its own adapter module under a typed
boundary defined in :mod:`app.platforms.base`. Product services must call
adapters through the :class:`~app.platforms.base.PlatformAdapter` protocol and
must never contain selectors, DOM traversal, or browser-specific retry logic
(see ``.trellis/spec/big-question/platform-integration-strategy.md``).
"""
