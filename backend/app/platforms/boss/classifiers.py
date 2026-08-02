"""Conservative page-state classifiers for BOSS Web.

Before any field is filled, the adapter calls :func:`classify_page` to decide
whether the page is safe to interact with. The classifier is deliberately
conservative: if it cannot *confidently* identify a safe fillable form, it
returns a hard-stop outcome. CAPTCHA, rate limits, login walls, duplicates, and
selector drift are never retried or worked around here — they are surfaced for
manual review.

The classifier reads only locator visibility (``is_visible`` / ``count``) plus
a short in-memory content snippet. It never persists any of this. The content
snippet is discarded as soon as classification completes.

Mapping to adapter outcomes:

| Classifier result      | PrepareOutcome          |
| ---------------------- | ----------------------- |
| login marker visible   | ``login_required``      |
| captcha marker visible | ``captcha_required``    |
| rate-limit marker      | ``rate_limited``        |
| duplicate marker       | ``duplicate_detected``  |
| required anchors miss  | ``selector_drift``      |
| form anchors visible   | ``form_ready``          |
| anything ambiguous     | ``unknown``             |
"""

from __future__ import annotations

from dataclasses import dataclass

from app.platforms.boss.runtime import BossPage
from app.platforms.boss.selectors import (
    CAPTCHA_MARKER,
    DUPLICATE_MARKER,
    FINAL_SUBMIT_BUTTON,
    LOGIN_MARKER,
    MESSAGE_INPUT,
    RATE_LIMIT_MARKER,
    Selector,
)


@dataclass(frozen=True)
class PageClassification:
    """Result of classifying a BOSS page.

    ``outcome`` is one of the seven classifier codes (``form_ready`` plus six
    hard stops). ``diagnostic_reference`` is an opaque local reference (never
    raw HTML) the adapter can attach to a failure result.
    """

    outcome: str
    diagnostic_reference: str | None = None


# Classifier outcome codes. Kept as plain strings (not an enum) so the adapter
# can map them directly to ``PrepareOutcome`` without an extra import cycle.
FORM_READY = "form_ready"
LOGIN_REQUIRED = "login_required"
CAPTCHA_REQUIRED = "captcha_required"
RATE_LIMITED = "rate_limited"
DUPLICATE_DETECTED = "duplicate_detected"
SELECTOR_DRIFT = "selector_drift"
UNKNOWN = "unknown"


async def _visible(page: BossPage, selector: Selector) -> bool:
    """Resolve a selector and return whether its locator is visible."""
    locator = _resolve(page, selector)
    try:
        return await page.is_visible(locator)
    except Exception:
        return False


async def _count(page: BossPage, selector: Selector) -> int:
    """Resolve a selector and return the number of matches (>0 => present)."""
    locator = _resolve(page, selector)
    try:
        return await page.count(locator)
    except Exception:
        return 0


def _resolve(page: BossPage, selector: Selector):
    """Resolve a :class:`Selector` against a :class:`BossPage`."""
    if selector.kind.value == "role":
        return page.get_by_role(selector.value, name=selector.name)
    if selector.kind.value == "label":
        return page.get_by_label(selector.value)
    if selector.kind.value == "placeholder":
        return page.get_by_placeholder(selector.value)
    return page.locator(selector.value)


async def classify_page(page: BossPage) -> PageClassification:
    """Classify the current BOSS page before any fill/click.

    The order matters: failure markers are checked *before* form anchors so a
    login wall or CAPTCHA is never mistaken for a fillable form.
    """
    # 1. Login wall.
    if await _visible(page, LOGIN_MARKER) or await _count(page, LOGIN_MARKER) > 0:
        return PageClassification(outcome=LOGIN_REQUIRED, diagnostic_reference="login_marker")

    # 2. CAPTCHA challenge.
    if await _visible(page, CAPTCHA_MARKER) or await _count(page, CAPTCHA_MARKER) > 0:
        return PageClassification(outcome=CAPTCHA_REQUIRED, diagnostic_reference="captcha_marker")

    # 3. Rate limit.
    if await _count(page, RATE_LIMIT_MARKER) > 0:
        return PageClassification(outcome=RATE_LIMITED, diagnostic_reference="rate_limit_marker")

    # 4. Already-applied / duplicate (HR conversation already started).
    if await _count(page, DUPLICATE_MARKER) > 0:
        return PageClassification(
            outcome=DUPLICATE_DETECTED, diagnostic_reference="duplicate_marker"
        )

    # 5. Required form anchors. Both the message input and the final submit
    #    button must be present. If either is missing, the BOSS markup has
    #    drifted and we must not guess.
    message_present = await _count(page, MESSAGE_INPUT) > 0 or await _visible(
        page, MESSAGE_INPUT
    )
    submit_present = await _count(page, FINAL_SUBMIT_BUTTON) > 0 or await _visible(
        page, FINAL_SUBMIT_BUTTON
    )
    if not (message_present and submit_present):
        return PageClassification(
            outcome=SELECTOR_DRIFT,
            diagnostic_reference="missing_form_anchors",
        )

    # 6. Confidently fillable.
    return PageClassification(outcome=FORM_READY)


async def classify_submit_result(page: BossPage) -> PageClassification:
    """Classify the page state *after* the final submit click.

    Used by :meth:`RealBossAdapter.submit_prepared` to decide whether the
    platform confirmed success, reported a duplicate, surfaced an error, or left
    us in an ambiguous state.
    """
    from app.platforms.boss.selectors import (
        PLATFORM_ERROR_MARKER,
        SUBMIT_DUPLICATE_MARKER,
        SUCCESS_MARKER,
    )

    if await _count(page, SUCCESS_MARKER) > 0:
        return PageClassification(outcome="submitted", diagnostic_reference="success_marker")

    if await _count(page, SUBMIT_DUPLICATE_MARKER) > 0:
        return PageClassification(
            outcome=DUPLICATE_DETECTED, diagnostic_reference="submit_duplicate_marker"
        )

    if await _count(page, PLATFORM_ERROR_MARKER) > 0:
        return PageClassification(
            outcome="platform_failure", diagnostic_reference="platform_error_marker"
        )

    # Ambiguous: no clear success, no clear failure. Hard stop for manual
    # reconciliation — never infer success from lack of error.
    return PageClassification(outcome=UNKNOWN, diagnostic_reference="no_confirmation")


__all__ = [
    "CAPTCHA_REQUIRED",
    "DUPLICATE_DETECTED",
    "FORM_READY",
    "LOGIN_REQUIRED",
    "PageClassification",
    "RATE_LIMITED",
    "SELECTOR_DRIFT",
    "UNKNOWN",
    "classify_page",
    "classify_submit_result",
]
