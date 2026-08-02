"""Tests for the real BOSS adapter (Playwright-backed) using fake Playwright objects.

The real adapter is env-gated and never touches a real browser in CI. These
tests inject a fake ``async_playwright`` factory + fake page/context objects so
the adapter's navigation, classification, fill, and submit logic is exercised
deterministically.

Safety invariants asserted here:

- prepare never clicks the final submit control.
- submit clicks the final submit control at most once.
- ``submitted`` is returned only on an observed success marker.
- No cookies/tokens/raw HTML/session paths appear in persisted results.
- Missing profile dir → ``login_required``.
- All seven classifier outcomes produce the right prepare/submit result.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from app.platforms.base import (
    FilledField,
    FilledPageState,
    FilledSubmissionSnapshot,
    PrepareContext,
    PrepareOutcome,
    SubmitContext,
    SubmitOutcome,
    prepare_failure_code,
    submit_failure_code,
)

# ---------------------------------------------------------------------------
# Fake Playwright objects
# ---------------------------------------------------------------------------


class FakeLocator:
    """A fake Playwright Locator with canned behavior."""

    def __init__(
        self,
        *,
        visible: bool = False,
        count: int = 0,
        fill_should_raise: bool = False,
        click_should_raise: bool = False,
    ) -> None:
        self._visible = visible
        self._count = count
        self._fill_should_raise = fill_should_raise
        self._click_should_raise = click_should_raise
        self.fill_calls: list[str] = []
        self.click_count = 0

    async def is_visible(self) -> bool:
        return self._visible

    async def count(self) -> int:
        return self._count

    async def fill(self, value: str) -> None:
        if self._fill_should_raise:
            raise RuntimeError("fill failed")
        self.fill_calls.append(value)

    async def click(self) -> None:
        if self._click_should_raise:
            raise RuntimeError("click failed")
        self.click_count += 1

    async def text_content(self) -> str | None:
        return None


class FakePlaywrightPage:
    """A fake Playwright Page that maps selector keys to FakeLocator objects."""

    def __init__(
        self,
        locators: dict[str, FakeLocator],
        *,
        url: str = "https://www.zhipin.com/job/123",
        title: str = "BOSS - 职位详情",
    ) -> None:
        self._locators = locators
        self.url = url
        self._title = title

    async def title(self) -> str:
        return self._title

    async def goto(self, url: str, *, timeout: float) -> None:
        self.url = url

    async def content(self) -> str:
        return "<html>fake</html>"

    def _lookup(self, value: str, *, name: str | None = None) -> FakeLocator:
        if value in self._locators:
            return self._locators[value]
        if name is not None and name in self._locators:
            return self._locators[name]
        return FakeLocator()

    def get_by_role(self, role: str, *, name: str | None = None) -> FakeLocator:
        return self._lookup(role, name=name)

    def get_by_label(self, text: str) -> FakeLocator:
        return self._lookup(text)

    def get_by_placeholder(self, text: str) -> FakeLocator:
        return self._lookup(text)

    def locator(self, selector: str) -> FakeLocator:
        return self._lookup(selector)

    async def wait_for_selector(self, selector: str, *, timeout: float) -> FakeLocator:
        return self._lookup(selector)

    async def close(self) -> None:
        pass


class FakePlaywrightContext:
    """A fake Playwright BrowserContext.

    ``pages`` exposes the context's existing pages so CDP mode (which reuses
    ``context.pages[0]`` instead of calling ``new_page``) works. In persistent
    mode ``new_page`` is called and the canned page is returned.
    """

    def __init__(self, page: FakePlaywrightPage) -> None:
        self._page = page
        self.closed = False
        # CDP mode reads ``context.pages`` to reuse the existing tab.
        self.pages: list[FakePlaywrightPage] = [page]

    async def new_page(self) -> FakePlaywrightPage:
        return self._page

    async def close(self) -> None:
        self.closed = True


class FakeCdpBrowser:
    """A fake CDP-connected browser returned by ``connect_over_cdp``.

    In CDP mode the runtime reuses the existing context (``contexts[0]``) and
    closes only the browser (the DevTools *client*) on exit — never the
    context. This fake records whether ``close`` was called and whether the
    context's ``close`` was *not* called, so tests can assert the user's
    Chrome is left intact.
    """

    def __init__(self, context: FakePlaywrightContext) -> None:
        self._context = context
        self.contexts: list[FakePlaywrightContext] = [context]
        self.closed = False

    async def close(self) -> None:
        # Closing a CDP browser only drops the DevTools connection; it must
        # NOT close the underlying context (the user's real Chrome).
        self.closed = True


class FakePlaywright:
    """A fake Playwright object exposing both persistent-context and CDP APIs."""

    def __init__(self, context: FakePlaywrightContext) -> None:
        self._context = context
        self.stopped = False
        self.cdp_browser: FakeCdpBrowser | None = None

    @property
    def chromium(self):
        outer = self

        class _Chromium:
            async def launch_persistent_context(self, **kwargs):
                return outer._context

            async def connect_over_cdp(self, endpoint: str):
                outer.cdp_browser = FakeCdpBrowser(outer._context)
                return outer.cdp_browser

        return _Chromium()

    async def stop(self) -> None:
        self.stopped = True


def _make_fake_playwright(
    locators: dict[str, FakeLocator],
    *,
    url: str = "https://www.zhipin.com/job/123",
    title: str = "BOSS - 职位详情",
):
    """Return an ``async_playwright``-compatible factory + the fake page.

    The factory is an async callable returning a :class:`FakePlaywright`. The
    fake page is returned so tests can inspect locator call counts.
    """
    page = FakePlaywrightPage(locators, url=url, title=title)
    context = FakePlaywrightContext(page)

    async def factory():
        return FakePlaywright(context)

    return factory, page, context


# ---------------------------------------------------------------------------
# Selector key constants (must match selectors.py values)
# ---------------------------------------------------------------------------

_MSG_KEY = "请输入你要发送的内容"  # MESSAGE_INPUT placeholder
_SUBMIT_KEY = "发送"  # FINAL_SUBMIT_BUTTON role name
_RESUME_KEY = "input[type='file'][accept*='pdf']"  # RESUME_UPLOAD css
_LOGIN_KEY = "[data-test='login-wrap'], .login-wrap, #wrap[data-type='login']"
_CAPTCHA_KEY = ".nc_iconfont, #nc_1_n1z, .captcha-widget, .geetest_widget"
_RATE_KEY = ".error-tip:has-text('频繁'), .rate-limit, .error-content:has-text('操作太频繁')"
_DUP_KEY = ".btn-start:has-text('继续沟通'), .chat-operate:has-text('继续沟通')"
_SUCCESS_KEY = (
    ".chat-operate:has-text('已发送'), .send-status:has-text('已发送'), "
    ".btn-start:has-text('继续沟通')"
)
_SUBMIT_DUP_KEY = ".error-tip:has-text('已投递'), .tip:has-text('已经投递')"
_PLATFORM_ERR_KEY = ".error-tip, .error-content, .upload-error"


def _real_ctx(**overrides: object) -> PrepareContext:
    base: dict[str, object] = {
        "application_id": "app-1",
        "target_platform": "boss",
        "target_resource": "https://www.zhipin.com/job/123",
        "selected_artifact_ids": ["art-1"],
        "outgoing_text": "您好，我对这个岗位很感兴趣。",
        "resume_file_reference": "resume-v1",
        "source_hash": "sha256:abc",
    }
    base.update(overrides)
    return PrepareContext(**base)  # type: ignore[arg-type]


def _make_snapshot(
    *,
    fields: list[FilledField] | None = None,
    target_resource: str = "https://www.zhipin.com/job/123",
) -> FilledSubmissionSnapshot:
    return FilledSubmissionSnapshot(
        target_platform="boss",
        target_resource=target_resource,
        application_id="app-1",
        selected_artifact_ids=["art-1"],
        resume_file_reference="resume-v1",
        fields=fields or [FilledField(name="message", label="开场白", value="您好")],
        attachments=[],
        page_state=FilledPageState(
            url_hash="sha256:abc", title="BOSS", final_submit_selector_seen=True
        ),
        captured_at=datetime.now(),
    )


# ---------------------------------------------------------------------------
# RealBossAdapter construction + profile-dir guard
# ---------------------------------------------------------------------------


def _build_adapter():
    """Construct a RealBossAdapter with the Playwright import stubbed out."""
    import sys
    import types

    fake_pw_mod = types.ModuleType("playwright")
    fake_async_mod = types.ModuleType("playwright.async_api")
    fake_async_mod.async_playwright = lambda: None  # placeholder; real factory injected per-test
    fake_pw_mod.async_api = fake_async_mod
    sys.modules["playwright"] = fake_pw_mod
    sys.modules["playwright.async_api"] = fake_async_mod
    try:
        from app.platforms.boss.adapter import RealBossAdapter

        return RealBossAdapter()
    finally:
        sys.modules.pop("playwright", None)
        sys.modules.pop("playwright.async_api", None)


async def test_prepare_returns_login_required_when_no_session_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both ``boss_session_profile_dir`` and ``boss_cdp_endpoint`` unset →
    ``login_required``, never filled_preview."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "")
    monkeypatch.setenv("BOSS_CDP_ENDPOINT", "")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        result = await adapter.prepare_submission(_real_ctx())
    finally:
        get_settings.cache_clear()
    assert result.outcome is PrepareOutcome.login_required
    assert result.snapshot is None
    assert result.failure_code == prepare_failure_code(PrepareOutcome.login_required)


# ---------------------------------------------------------------------------
# Prepare: filled preview + stop-before-submit
# ---------------------------------------------------------------------------


async def test_prepare_returns_filled_preview_and_never_clicks_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A form-ready page produces a filled_preview snapshot; the final submit
    control is never clicked during prepare."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/fake-profile")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        submit_locator = FakeLocator(visible=True, count=1)
        message_locator = FakeLocator(visible=True, count=1)
        locators = {
            _MSG_KEY: message_locator,
            _SUBMIT_KEY: submit_locator,
            _RESUME_KEY: FakeLocator(visible=True, count=1),
        }
        factory, _page, _ctx = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.prepare_submission(_real_ctx())
    finally:
        get_settings.cache_clear()

    assert result.outcome is PrepareOutcome.filled_preview
    assert result.snapshot is not None
    assert result.snapshot.page_state.final_submit_selector_seen is True
    # The final submit control was never clicked during prepare.
    assert submit_locator.click_count == 0
    # The message was filled.
    assert message_locator.fill_calls == ["您好，我对这个岗位很感兴趣。"]


async def test_prepare_snapshot_has_no_secrets_or_raw_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The persisted snapshot must not contain raw URLs, cookies, tokens, or
    the profile directory path."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/secret-profile-path")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        locators = {
            _MSG_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_KEY: FakeLocator(visible=True, count=1),
        }
        factory, _page, _ctx = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.prepare_submission(_real_ctx())
    finally:
        get_settings.cache_clear()

    assert result.outcome is PrepareOutcome.filled_preview
    snapshot = result.snapshot
    assert snapshot is not None
    # ``target_resource`` is the user's own job posting URL / conversation id —
    # it legitimately appears in the snapshot by design. What must NOT leak is
    # the session profile path, cookies, tokens, raw HTML, or the URL inside
    # ``page_state`` (which carries only a hash).
    assert snapshot.page_state.url_hash.startswith("sha256:")
    assert snapshot.page_state.url_hash != snapshot.target_resource
    blob = repr(snapshot.model_dump(mode="json"))
    for forbidden in ("secret-profile-path", "cookie", "token", "<html"):
        assert forbidden not in blob.lower(), f"snapshot leaked: {forbidden}"


# ---------------------------------------------------------------------------
# Prepare: seven failure categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker_key, expected_outcome",
    [
        (_LOGIN_KEY, PrepareOutcome.login_required),
        (_CAPTCHA_KEY, PrepareOutcome.captcha_required),
        (_RATE_KEY, PrepareOutcome.rate_limited),
        (_DUP_KEY, PrepareOutcome.duplicate_detected),
    ],
)
async def test_prepare_classifies_failure_markers_before_fill(
    marker_key: str,
    expected_outcome: PrepareOutcome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Login/CAPTCHA/rate-limit/duplicate markers are hard stops, even when form
    anchors are also present."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/fake-profile")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        locators = {
            marker_key: FakeLocator(visible=True, count=1),
            _MSG_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_KEY: FakeLocator(visible=True, count=1),
        }
        factory, _page, _ctx = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.prepare_submission(_real_ctx())
    finally:
        get_settings.cache_clear()

    assert result.outcome is expected_outcome
    assert result.snapshot is None
    assert result.failure_code == prepare_failure_code(expected_outcome)


async def test_prepare_returns_selector_drift_when_anchors_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing form anchors → selector_drift."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/fake-profile")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        # No message input, no submit button.
        factory, _page, _ctx = _make_fake_playwright({})

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.prepare_submission(_real_ctx())
    finally:
        get_settings.cache_clear()

    assert result.outcome is PrepareOutcome.selector_drift
    assert result.failure_code == prepare_failure_code(PrepareOutcome.selector_drift)


# ---------------------------------------------------------------------------
# Submit: at most one click + result classification
# ---------------------------------------------------------------------------


async def test_submit_clicks_final_submit_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final submit control is clicked exactly once per submit call."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/fake-profile")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        submit_locator = FakeLocator(visible=True, count=1)
        message_locator = FakeLocator(visible=True, count=1)
        locators = {
            _MSG_KEY: message_locator,
            _SUBMIT_KEY: submit_locator,
            _SUCCESS_KEY: FakeLocator(count=1),
        }
        factory, _page, _ctx = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.submit_prepared(
                SubmitContext(
                    application_id="app-1",
                    target_platform="boss",
                    target_resource="https://www.zhipin.com/job/123",
                    filled_snapshot=_make_snapshot(),
                )
            )
    finally:
        get_settings.cache_clear()

    assert result.outcome is SubmitOutcome.submitted
    assert submit_locator.click_count == 1


async def test_submit_returns_submitted_only_on_success_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``submitted`` requires an observed success marker, not just a lack of error."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/fake-profile")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        submit_locator = FakeLocator(visible=True, count=1)
        # No success marker present → ambiguous → unknown.
        locators = {
            _MSG_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_KEY: submit_locator,
        }
        factory, _page, _ctx = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.submit_prepared(
                SubmitContext(
                    application_id="app-1",
                    target_platform="boss",
                    target_resource="https://www.zhipin.com/job/123",
                    filled_snapshot=_make_snapshot(),
                )
            )
    finally:
        get_settings.cache_clear()

    assert result.outcome is SubmitOutcome.unknown
    assert result.failure_code == submit_failure_code(SubmitOutcome.unknown)


async def test_submit_returns_duplicate_on_duplicate_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/fake-profile")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        locators = {
            _MSG_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_DUP_KEY: FakeLocator(count=1),
        }
        factory, _page, _ctx = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.submit_prepared(
                SubmitContext(
                    application_id="app-1",
                    target_platform="boss",
                    target_resource="https://www.zhipin.com/job/123",
                    filled_snapshot=_make_snapshot(),
                )
            )
    finally:
        get_settings.cache_clear()

    assert result.outcome is SubmitOutcome.duplicate_detected
    assert result.failure_code == submit_failure_code(SubmitOutcome.duplicate_detected)


async def test_submit_returns_platform_failure_on_error_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/fake-profile")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        locators = {
            _MSG_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_KEY: FakeLocator(visible=True, count=1),
            _PLATFORM_ERR_KEY: FakeLocator(count=1),
        }
        factory, _page, _ctx = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.submit_prepared(
                SubmitContext(
                    application_id="app-1",
                    target_platform="boss",
                    target_resource="https://www.zhipin.com/job/123",
                    filled_snapshot=_make_snapshot(),
                )
            )
    finally:
        get_settings.cache_clear()

    assert result.outcome is SubmitOutcome.platform_failure
    assert result.failure_code == submit_failure_code(SubmitOutcome.platform_failure)


async def test_submit_returns_unknown_when_no_session_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both ``boss_session_profile_dir`` and ``boss_cdp_endpoint`` unset during
    submit → unknown (cannot open session)."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "")
    monkeypatch.setenv("BOSS_CDP_ENDPOINT", "")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        result = await adapter.submit_prepared(
            SubmitContext(
                application_id="app-1",
                target_platform="boss",
                target_resource="https://www.zhipin.com/job/123",
                filled_snapshot=_make_snapshot(),
            )
        )
    finally:
        get_settings.cache_clear()

    assert result.outcome is SubmitOutcome.unknown


async def test_submit_no_secrets_in_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The submit result must not contain raw URLs, cookies, tokens, or profile paths."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "/tmp/secret-profile-path")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        locators = {
            _MSG_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_KEY: FakeLocator(visible=True, count=1),
            _SUCCESS_KEY: FakeLocator(count=1),
        }
        factory, _page, _ctx = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", cdp_endpoint),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.submit_prepared(
                SubmitContext(
                    application_id="app-1",
                    target_platform="boss",
                    target_resource="https://www.zhipin.com/job/123",
                    filled_snapshot=_make_snapshot(),
                )
            )
    finally:
        get_settings.cache_clear()

    blob = repr(result.model_dump(mode="json"))
    for forbidden in ("zhipin.com", "secret-profile-path", "cookie", "token", "<html"):
        assert forbidden not in blob.lower(), f"submit result leaked: {forbidden}"


# ---------------------------------------------------------------------------
# CDP mode: connect to already-running Chrome, never close the user's context
# ---------------------------------------------------------------------------


async def test_prepare_cdp_connects_and_does_not_close_user_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CDP mode: the runtime connects via ``connect_over_cdp``, reuses the
    existing context/page, and on exit only disconnects — the user's Chrome
    context is never closed."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "")
    monkeypatch.setenv("BOSS_CDP_ENDPOINT", "http://127.0.0.1:9222")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        submit_locator = FakeLocator(visible=True, count=1)
        message_locator = FakeLocator(visible=True, count=1)
        locators = {
            _MSG_KEY: message_locator,
            _SUBMIT_KEY: submit_locator,
        }
        factory, _page, context = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", "http://127.0.0.1:9222"),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.prepare_submission(_real_ctx())
    finally:
        get_settings.cache_clear()

    assert result.outcome is PrepareOutcome.filled_preview
    # The user's Chrome context must NOT be closed in CDP mode.
    assert context.closed is False
    # The final submit control was never clicked during prepare.
    assert submit_locator.click_count == 0


async def test_submit_cdp_clicks_once_and_does_not_close_user_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CDP mode submit: clicks exactly once, leaves the user's Chrome context
    open, and disconnects the CDP client."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "")
    monkeypatch.setenv("BOSS_CDP_ENDPOINT", "http://127.0.0.1:9222")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        submit_locator = FakeLocator(visible=True, count=1)
        locators = {
            _MSG_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_KEY: submit_locator,
            _SUCCESS_KEY: FakeLocator(count=1),
        }
        factory, _page, context = _make_fake_playwright(locators)

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", "http://127.0.0.1:9222"),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.submit_prepared(
                SubmitContext(
                    application_id="app-1",
                    target_platform="boss",
                    target_resource="https://www.zhipin.com/job/123",
                    filled_snapshot=_make_snapshot(),
                )
            )
    finally:
        get_settings.cache_clear()

    assert result.outcome is SubmitOutcome.submitted
    # Exactly one click on the final submit control.
    assert submit_locator.click_count == 1
    # The user's Chrome context must NOT be closed in CDP mode.
    assert context.closed is False


async def test_cdp_mode_reuses_existing_page_not_new_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In CDP mode the runtime reuses ``context.pages[0]`` rather than calling
    ``new_page`` — it never creates a new tab in the user's Chrome."""
    from app.core.config import get_settings

    monkeypatch.setenv("BOSS_SESSION_PROFILE_DIR", "")
    monkeypatch.setenv("BOSS_CDP_ENDPOINT", "http://127.0.0.1:9222")
    get_settings.cache_clear()
    adapter = _build_adapter()
    try:
        new_page_calls = []

        locators = {
            _MSG_KEY: FakeLocator(visible=True, count=1),
            _SUBMIT_KEY: FakeLocator(visible=True, count=1),
        }
        factory, page, context = _make_fake_playwright(locators)
        original_new_page = context.new_page

        async def counting_new_page():
            new_page_calls.append(1)
            return await original_new_page()

        context.new_page = counting_new_page

        with patch(
            "app.platforms.boss.runtime.BossBrowserRuntime.__init__",
            lambda self, *, profile_dir="", cdp_endpoint="", async_playwright=None: (
                setattr(self, "_profile_dir", profile_dir),
                setattr(self, "_cdp_endpoint", "http://127.0.0.1:9222"),
                setattr(self, "_async_playwright", factory),
                None,
            )[-1],
        ):
            result = await adapter.prepare_submission(_real_ctx())
    finally:
        get_settings.cache_clear()

    assert result.outcome is PrepareOutcome.filled_preview
    # CDP mode must reuse the existing page — never call new_page.
    assert new_page_calls == []
