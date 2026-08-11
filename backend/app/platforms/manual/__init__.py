"""Manual (generate-and-copy) platform package.

The manual adapter is a first-class :class:`~app.platforms.base.PlatformAdapter`
— not a degraded fallback. It models the "generate → copy → user pastes"
delivery path: the product generates copy-ready artifacts (opening message,
targeted resume), the user copies them and pastes into any platform by hand.

This path is platform-agnostic by construction and never touches a browser,
so it stays fully functional whenever automated adapters are unavailable
(bridge disconnected, selectors drifted, platform risk-control tightened).
"""
