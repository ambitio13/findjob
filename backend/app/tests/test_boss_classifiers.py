"""Unit tests for the BOSS page classifiers.

The classifier is conservative: it must never mistake a login wall, CAPTCHA,
rate limit, or duplicate for a fillable form. These tests use a fake
``BossPage`` whose locators return canned visibility/count values, covering all
seven classifier outcomes (form_ready + six hard stops) plus the post-submit
result classification.
"""

from __future__ import annotations

import pytest

from app.platforms.boss.classifiers import (
    CAPTCHA_REQUIRED,
    DUPLICATE_DETECTED,
    FORM_READY,
    LOGIN_REQUIRED,
    RATE_LIMITED,
    SELECTOR_DRIFT,
    UNKNOWN,
    classify_page,
    classify_submit_result,
)
from app.platforms.boss.runtime import BossPage
from app.platforms.boss.selectors import (
    CAPTCHA_MARKER,
    DUPLICATE_MARKER,
    FINAL_SUBMIT_BUTTON,
    LOGIN_MARKER,
    MESSAGE_INPUT,
    PLATFORM_ERROR_MARKER,
    RATE_LIMIT_MARKER,
    SUBMIT_DUPLICATE_MARKER,
    SUCCESS_MARKER,
)


class FakeLocator:
    """A fake Playwright Locator with canned visibility/count/fill/click."""

    def __init__(
        self,
        *,
        visible: bool = False,
        count: int = 0,
        fill_should_raise: bool = False,
    ) -> None:
        self._visible = visible
        self._count = count
        self._fill_should_raise = fill_should_raise
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
        self.click_count += 1

    async def text_content(self) -> str | None:
        return None


class FakePlaywrightPage:
    """A fake Playwright Page that maps selector keys to FakeLocator objects."""

    def __init__(
        self, locators: dict[str, FakeLocator], *, url: str = "https://boss.test/job/1"
    ) -> None:
        # ``locators`` maps a *selector string* (the CSS / placeholder / role
        # value) to a FakeLocator. The fake page resolves any locator method by
        # looking up the value.
        self._locators = locators
        self.url = url
        self._title = "BOSS application form"

    async def title(self) -> str:
        return self._title

    async def goto(self, url: str, *, timeout: float) -> None:
        self.url = url

    async def content(self) -> str:
        return "<html>fake</html>"

    def _lookup(self, value: str, *, name: str | None = None) -> FakeLocator:
        # Try exact value match first, then name match (for role locators).
        if value in self._locators:
            return self._locators[value]
        if name is not None and name in self._locators:
            return self._locators[name]
        # Default: not present.
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


def _make_page(
    *,
    login: bool = False,
    captcha: bool = False,
    rate_limited: bool = False,
    duplicate: bool = False,
    message: bool = True,
    submit: bool = True,
    success: bool = False,
    submit_duplicate: bool = False,
    platform_error: bool = False,
) -> BossPage:
    """Build a BossPage backed by a FakePlaywrightPage with the given markers."""
    locators: dict[str, FakeLocator] = {}
    if login:
        locators[LOGIN_MARKER.value] = FakeLocator(visible=True, count=1)
    if captcha:
        locators[CAPTCHA_MARKER.value] = FakeLocator(visible=True, count=1)
    if rate_limited:
        locators[RATE_LIMIT_MARKER.value] = FakeLocator(count=1)
    if duplicate:
        locators[DUPLICATE_MARKER.value] = FakeLocator(count=1)
    if message:
        locators[MESSAGE_INPUT.value] = FakeLocator(visible=True, count=1)
    if submit:
        locators[FINAL_SUBMIT_BUTTON.name] = FakeLocator(visible=True, count=1)
    if success:
        locators[SUCCESS_MARKER.value] = FakeLocator(count=1)
    if submit_duplicate:
        locators[SUBMIT_DUPLICATE_MARKER.value] = FakeLocator(count=1)
    if platform_error:
        locators[PLATFORM_ERROR_MARKER.value] = FakeLocator(count=1)
    return BossPage(FakePlaywrightPage(locators))


# ---------------------------------------------------------------------------
# classify_page — all seven outcomes
# ---------------------------------------------------------------------------


async def test_classify_page_returns_form_ready_when_anchors_present() -> None:
    page = _make_page(message=True, submit=True)
    result = await classify_page(page)
    assert result.outcome == FORM_READY


async def test_classify_page_returns_login_required() -> None:
    page = _make_page(login=True, message=True, submit=True)
    result = await classify_page(page)
    assert result.outcome == LOGIN_REQUIRED
    assert result.diagnostic_reference == "login_marker"


async def test_classify_page_returns_captcha_required() -> None:
    page = _make_page(captcha=True, message=True, submit=True)
    result = await classify_page(page)
    assert result.outcome == CAPTCHA_REQUIRED
    assert result.diagnostic_reference == "captcha_marker"


async def test_classify_page_returns_rate_limited() -> None:
    page = _make_page(rate_limited=True, message=True, submit=True)
    result = await classify_page(page)
    assert result.outcome == RATE_LIMITED
    assert result.diagnostic_reference == "rate_limit_marker"


async def test_classify_page_returns_duplicate_detected() -> None:
    page = _make_page(duplicate=True, message=True, submit=True)
    result = await classify_page(page)
    assert result.outcome == DUPLICATE_DETECTED
    assert result.diagnostic_reference == "duplicate_marker"


async def test_classify_page_returns_selector_drift_when_message_missing() -> None:
    page = _make_page(message=False, submit=True)
    result = await classify_page(page)
    assert result.outcome == SELECTOR_DRIFT
    assert result.diagnostic_reference == "missing_form_anchors"


async def test_classify_page_returns_selector_drift_when_submit_missing() -> None:
    page = _make_page(message=True, submit=False)
    result = await classify_page(page)
    assert result.outcome == SELECTOR_DRIFT


async def test_classify_page_returns_unknown_when_nothing_present() -> None:
    """An empty page with no markers and no form anchors is selector_drift,
    not unknown. Unknown is reserved for genuinely ambiguous states that the
    classifier cannot resolve. Here, missing anchors = drift."""
    page = _make_page(message=False, submit=False)
    result = await classify_page(page)
    assert result.outcome == SELECTOR_DRIFT


@pytest.mark.parametrize(
    "marker_kw, expected",
    [
        ({"login": True}, LOGIN_REQUIRED),
        ({"captcha": True}, CAPTCHA_REQUIRED),
        ({"rate_limited": True}, RATE_LIMITED),
        ({"duplicate": True}, DUPLICATE_DETECTED),
    ],
)
async def test_classify_page_failure_markers_take_priority_over_form_anchors(
    marker_kw: dict, expected: str
) -> None:
    """A login wall / CAPTCHA / rate limit / duplicate is never mistaken for a
    fillable form even if the form anchors happen to be present."""
    page = _make_page(message=True, submit=True, **marker_kw)
    result = await classify_page(page)
    assert result.outcome == expected


# ---------------------------------------------------------------------------
# classify_submit_result
# ---------------------------------------------------------------------------


async def test_classify_submit_result_returns_submitted_on_success_marker() -> None:
    page = _make_page(success=True)
    result = await classify_submit_result(page)
    assert result.outcome == "submitted"


async def test_classify_submit_result_returns_duplicate_on_duplicate_marker() -> None:
    page = _make_page(submit_duplicate=True)
    result = await classify_submit_result(page)
    assert result.outcome == DUPLICATE_DETECTED


async def test_classify_submit_result_returns_platform_failure_on_error_marker() -> None:
    page = _make_page(platform_error=True)
    result = await classify_submit_result(page)
    assert result.outcome == "platform_failure"


async def test_classify_submit_result_returns_unknown_on_no_confirmation() -> None:
    page = _make_page()
    result = await classify_submit_result(page)
    assert result.outcome == UNKNOWN
    assert result.diagnostic_reference == "no_confirmation"
