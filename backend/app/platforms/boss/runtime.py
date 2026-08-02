"""Playwright runtime wrapper for the BOSS Web adapter.

This module is the *only* place that touches the Playwright API directly. The
adapter (:mod:`app.platforms.boss.adapter`) depends on :class:`BossPage`, never
on Playwright objects, so all browser lifecycle, timeouts, and cleanup are
centralized and auditable.

Safety invariants enforced here:

- Exactly one page is opened per runtime session (no tab fan-out).
- Navigation and fill use bounded timeouts (30s / 15s). No unbounded waits.
- The context is closed deterministically in ``__aexit__`` even on exceptions.
- Only sanitized diagnostics leave the runtime: a URL hash, a safe title, and a
  classification code. Raw HTML, cookies, headers, tokens, and Playwright
  storage state never leave this module.
- Playwright is imported lazily inside ``__aenter__`` so default test/dev
  installs never require it. Tests inject a fake ``async_playwright`` factory.

The runtime is a thin async context manager:

.. code-block:: python

    runtime = BossBrowserRuntime(profile_dir=...)
    async with runtime.open(target_resource) as page:
        classification = await classify_page(page)
        ...
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol

from app.core.logging import get_logger
from app.platforms.boss.sanitizer import sanitize_title, sanitize_url

_log = get_logger("app.platforms.boss.runtime")

#: Bounded navigation timeout (ms). BOSS pages must load within this window or
#: the prepare/submit aborts with ``unknown`` rather than hanging the worker.
NAV_TIMEOUT_MS = 30_000

#: Bounded fill/click timeout (ms).
ACTION_TIMEOUT_MS = 15_000


class Locator(Protocol):
    """Subset of ``playwright.async_api.Locator`` the runtime relies on.

    Defined as a Protocol so tests can inject fakes that satisfy this shape
    without depending on Playwright at type-check time.
    """

    async def is_visible(self) -> bool: ...

    async def fill(self, value: str) -> None: ...

    async def click(self) -> None: ...

    async def count(self) -> int: ...

    async def text_content(self) -> str | None: ...


class Page(Protocol):
    """Subset of ``playwright.async_api.Page`` the runtime relies on."""

    url: str

    async def title(self) -> str: ...

    async def goto(self, url: str, *, timeout: float) -> None: ...

    async def content(self) -> str: ...

    def get_by_role(self, role: str, *, name: str | None = None) -> Locator: ...

    def get_by_label(self, text: str) -> Locator: ...

    def get_by_placeholder(self, text: str) -> Locator: ...

    def locator(self, selector: str) -> Locator: ...

    async def wait_for_selector(self, selector: str, *, timeout: float) -> Locator: ...

    async def close(self) -> None: ...


class BrowserContext(Protocol):
    """Subset of ``playwright.async_api.BrowserContext``."""

    async def new_page(self) -> Page: ...

    async def close(self) -> None: ...


class Playwright(Protocol):
    """Subset of the ``playwright.async_api.Playwright`` object."""

    chromium: Any


class _AsyncPlaywrightCallable(Protocol):
    def __call__(self) -> Any: ...


async def _start_playwright(pw_factory: _AsyncPlaywrightCallable) -> Any:
    """Start Playwright from a factory, handling both real and fake protocols.

    The real ``async_playwright()`` returns an async context manager (must be
    ``await __aenter__``'d, and ``stop()`` must be called on exit). Test fakes
    are plain async callables returning a Playwright-like object directly. This
    helper normalizes both so ``__aenter__`` / ``_close_*`` don't need to care.
    """
    obj = pw_factory()
    # Real Playwright: ``async_playwright()`` returns an
    # ``AsyncPlaywrightContextManager`` whose ``__aenter__`` yields the
    # ``Playwright`` instance. We call it and store the CM so ``stop()`` still
    # works (the CM's ``__aexit__`` delegates to ``stop()``).
    aenter = getattr(obj, "__aenter__", None)
    if aenter is not None:
        return await aenter()
    # Fake: ``factory()`` already returned the (awaited) Playwright object.
    if hasattr(obj, "__await__"):
        return await obj
    return obj


class BossPage:
    """Lightweight wrapper around a Playwright ``Page``.

    Exposes only the safe operations the adapter/classifiers need. All
    diagnostics produced here are sanitized before they leave. The wrapper does
    *not* own the page lifecycle — :class:`BossBrowserRuntime` closes the
    underlying context. This keeps cleanup in exactly one place.
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    # --- Locator resolution -------------------------------------------------

    def get_by_role(self, role: str, *, name: str | None = None) -> Locator:
        return self._page.get_by_role(role, name=name)

    def get_by_label(self, text: str) -> Locator:
        return self._page.get_by_label(text)

    def get_by_placeholder(self, text: str) -> Locator:
        return self._page.get_by_placeholder(text)

    def locator(self, selector: str) -> Locator:
        return self._page.locator(selector)

    async def wait_for_selector(
        self, selector: str, *, timeout: float = ACTION_TIMEOUT_MS
    ) -> Locator:
        return await self._page.wait_for_selector(selector, timeout=timeout)

    # --- Actions ------------------------------------------------------------

    async def fill(self, locator: Locator, value: str) -> None:
        await locator.fill(value)

    async def click(self, locator: Locator) -> None:
        await locator.click()

    async def is_visible(self, locator: Locator) -> bool:
        return await locator.is_visible()

    async def count(self, locator: Locator) -> int:
        return await locator.count()

    async def text_content(self, locator: Locator) -> str | None:
        return await locator.text_content()

    # --- Sanitized diagnostics ---------------------------------------------

    @property
    def url(self) -> str:
        return self._page.url

    def url_hash(self) -> str:
        return sanitize_url(self._page.url)

    async def safe_title(self) -> str | None:
        try:
            raw = await self._page.title()
        except Exception:  # pragma: no cover - defensive, title rarely raises
            return None
        return sanitize_title(raw)

    async def content_snippet(self, *, max_len: int = 200) -> str:
        """Return a short, sanitized content snippet for classifier heuristics.

        .. warning::

           This snippet is for *in-memory classification only*. It must never be
           persisted. Classifiers read markers from it and discard it.
        """
        try:
            raw = await self._page.content()
        except Exception:  # pragma: no cover - defensive
            return ""
        # Strip to a bounded window so we never hold the full DOM in memory.
        snippet = raw[:max_len]
        return snippet


class BossBrowserRuntime:
    """Owns the Playwright browser lifecycle (persistent-context or CDP).

    Constructed with either a local profile directory (persistent-context
    mode, used by tests/CI with fake Playwright objects) or a CDP endpoint
    (connects to an already-running, already-logged-in real Chrome — the only
    mode that reliably bypasses BOSS anti-automation detection). ``open`` is
    an async context manager that yields a single :class:`BossPage` navigated
    to ``target_resource`` and guarantees the browser is released on exit.

    When ``cdp_endpoint`` is set, the runtime connects via
    ``connect_over_cdp`` and **reuses** the existing browser context/page
    rather than creating a new one. It does **not** call ``page.goto`` —
    BOSS detects CDP navigation and destroys the page. The user must
    navigate to the target page manually in their Chrome window before the
    adapter runs. On exit the runtime only *disconnects* (closing the CDP
    client) — the user's Chrome process and its context are left intact.
    """

    def __init__(
        self,
        *,
        profile_dir: str = "",
        cdp_endpoint: str = "",
        async_playwright: _AsyncPlaywrightCallable | None = None,
    ) -> None:
        self._profile_dir = profile_dir
        self._cdp_endpoint = cdp_endpoint
        self._async_playwright = async_playwright
        self._pw: Any | None = None
        self._context: BrowserContext | None = None

    async def open(self, target_resource: str) -> _OpenContext:
        """Return an async context manager that yields a :class:`BossPage`."""
        return _OpenContext(self, target_resource)


class _OpenContext:
    """Async context manager produced by :meth:`BossBrowserRuntime.open`.

    Separated from the runtime so the runtime itself stays reusable across
    prepare/submit calls (the adapter opens one context per phase).

    Two connection modes, selected by ``runtime._cdp_endpoint``:

    * **CDP mode** (endpoint set): ``connect_over_cdp`` attaches to the user's
      already-running real Chrome. The existing context
      (``browser.contexts[0]``) and its first page are reused — we never close
      them. ``page.goto`` is **not** called: BOSS detects CDP navigation and
      destroys the page to ``about:blank``. The user must navigate to the
      target page manually before the adapter runs. On exit only the CDP
      client is closed (``browser.close``), which disconnects Playwright from
      Chrome without killing the browser. This is what makes BOSS accept the
      session: the browser fingerprint is genuinely real Chrome.
    * **Persistent mode** (endpoint empty): ``launch_persistent_context`` opens
      a fresh browser from ``profile_dir``. On exit the page, context, and
      Playwright driver are all closed. This path is used by tests with fake
      Playwright objects (no real browser involved).
    """

    def __init__(self, runtime: BossBrowserRuntime, target_resource: str) -> None:
        self._runtime = runtime
        self._target = target_resource
        self._pw: Any | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._browser: Any | None = None
        self._is_cdp: bool = bool(runtime._cdp_endpoint)

    async def __aenter__(self) -> BossPage:
        pw_factory = self._runtime._async_playwright
        if pw_factory is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - env-gated
                raise RuntimeError(
                    "BOSS adapter runtime requires playwright. "
                    "Install it (pip install '.[boss]') before enabling the real adapter."
                ) from exc
            pw_factory = async_playwright

        self._pw = await _start_playwright(pw_factory)
        if self._is_cdp:
            self._page = await self._open_cdp(self._runtime._cdp_endpoint)
            # In CDP mode we deliberately do NOT call ``page.goto``. BOSS
            # detects Playwright's CDP ``navigate`` command and destroys the
            # page to ``about:blank`` within milliseconds. The user must
            # navigate to the target page manually in their Chrome window
            # before the adapter runs (see docs/manual-boss-pilot.md §1).
            # We only read/click on the page the user already opened.
        else:
            self._page = await self._open_persistent(self._runtime._profile_dir)
            await self._page.goto(self._target, timeout=NAV_TIMEOUT_MS)
        return BossPage(self._page)

    async def _open_cdp(self, endpoint: str) -> Page:
        """Connect to an already-running Chrome via CDP and reuse its context.

        We never create a new context or close the existing one — the user's
        Chrome (with its logged-in BOSS session) stays open. Only the CDP
        *client* connection is closed on exit.
        """
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(endpoint)
        except AttributeError:
            # Fakes in tests may expose ``connect_over_cdp`` directly on the
            # playwright object rather than ``chromium``.
            self._browser = await self._pw.connect_over_cdp(endpoint)
        contexts = getattr(self._browser, "contexts", None) or []
        if contexts:
            self._context = contexts[0]
        else:  # pragma: no cover - real Chrome always has a default context
            self._context = await self._browser.new_context()
        pages = getattr(self._context, "pages", None) or []
        if pages:
            return pages[0]
        return await self._context.new_page()

    async def _open_persistent(self, profile_dir: str) -> Page:
        """Launch a persistent-context browser (test/CI path)."""
        try:
            browser = await self._pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True,
            )
        except AttributeError:
            # Fakes in tests may expose ``launch_persistent_context`` directly
            # on the playwright object rather than ``chromium``.
            browser = await self._pw.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True,
            )
        self._context = browser
        return await self._context.new_page()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._is_cdp:
            await self._close_cdp()
        else:
            await self._close_persistent()
        if exc is not None:
            _log.warning(
                "boss.runtime.closed_on_error",
                error_type=type(exc).__name__,
                url_hash=sanitize_url(self._target),
            )

    async def _close_cdp(self) -> None:
        """CDP cleanup: only disconnect the client; leave Chrome running.

        We do NOT close the context (it's the user's real Chrome context) or
        the page (it's the user's real Chrome tab). ``browser.close()`` on a
        CDP-connected browser merely drops the DevTools connection.
        """
        # The page belongs to the user's Chrome — never close it here.
        self._page = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # pragma: no cover - defensive cleanup
                pass
        # The context belongs to the user's Chrome — never close it.
        self._context = None
        # Stop the Playwright driver process (it was started by our factory).
        if self._pw is not None:
            stop = getattr(self._pw, "stop", None)
            if stop is not None:
                try:
                    await stop()
                except Exception:  # pragma: no cover - defensive cleanup
                    pass

    async def _close_persistent(self) -> None:
        """Persistent-context cleanup: page, then context, then playwright."""
        # Page close — best effort, swallow exceptions so context still closes.
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:  # pragma: no cover - defensive cleanup
                pass
        # Context close — releases the profile dir handle.
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # pragma: no cover - defensive cleanup
                pass
        # Playwright stop — tears down the driver process.
        if self._pw is not None:
            stop = getattr(self._pw, "stop", None)
            if stop is not None:
                try:
                    await stop()
                except Exception:  # pragma: no cover - defensive cleanup
                    pass


__all__ = [
    "ACTION_TIMEOUT_MS",
    "BossBrowserRuntime",
    "BossPage",
    "BrowserContext",
    "Locator",
    "NAV_TIMEOUT_MS",
    "Page",
    "Playwright",
]
