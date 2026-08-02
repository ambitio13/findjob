"""Centralized BOSS Web selector registry.

All BOSS-specific selectors live here — product services never reference DOM
structure. Selectors are kept in code (never in DB) so a selector drift is a
code change reviewed by humans, not a silent data migration.

Strategy: prefer stable semantic locators (role/label/placeholder) over brittle
CSS chains. Each entry records the *kind* of locator so the runtime can pick the
right Playwright API (``get_by_role`` / ``get_by_label`` / ``get_by_placeholder``
/ CSS). When BOSS ships a new markup variant, add a new entry here and return
``selector_drift`` from the classifier when the form anchors are missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class LocatorKind(StrEnum):
    """How a selector entry should be resolved by the runtime."""

    ROLE = "role"  # get_by_role(role, name=...)
    LABEL = "label"  # get_by_label(text)
    PLACEHOLDER = "placeholder"  # get_by_placeholder(text)
    CSS = "css"  # page.locator(css)


@dataclass(frozen=True)
class Selector:
    """One selector entry: kind + the value needed to resolve it."""

    kind: LocatorKind
    value: str
    # For role locators, an optional accessible name.
    name: str | None = None

    def resolve_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for the corresponding Playwright locator method."""
        if self.kind is LocatorKind.ROLE:
            kwargs: dict[str, Any] = {"name": self.name} if self.name else {}
            return {"role": self.value, **kwargs}
        return {"value": self.value}


# --- Form anchors (required for ``form_ready``) -----------------------------

#: The outgoing HR message input. Required when ``outgoing_text`` is present.
MESSAGE_INPUT = Selector(
    kind=LocatorKind.PLACEHOLDER,
    value="请输入你要发送的内容",
    name=None,
)

#: The resume file upload input. Optional — only used when
#: ``resume_file_reference`` is present and the widget is visible.
RESUME_UPLOAD = Selector(
    kind=LocatorKind.CSS,
    value="input[type='file'][accept*='pdf']",
    name=None,
)

#: The final submit control. Must be *visible* (never clicked) at prepare time,
#: clicked exactly once at submit time.
FINAL_SUBMIT_BUTTON = Selector(
    kind=LocatorKind.ROLE,
    value="button",
    name="发送",
)


# --- Failure markers (classify before fill) ---------------------------------

#: Login / unauthenticated marker. Visible on the BOSS login wall.
LOGIN_MARKER = Selector(
    kind=LocatorKind.CSS,
    value="[data-test='login-wrap'], .login-wrap, #wrap[data-type='login']",
    name=None,
)

#: CAPTCHA / challenge marker.
CAPTCHA_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=".nc_iconfont, #nc_1_n1z, .captcha-widget, .geetest_widget",
    name=None,
)

#: Rate-limit marker (BOSS shows this when the account is throttled).
RATE_LIMIT_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=".error-tip:has-text('频繁'), .rate-limit, .error-content:has-text('操作太频繁')",
    name=None,
)

#: Already-applied / duplicate marker (HR conversation already started).
DUPLICATE_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=".btn-start:has-text('继续沟通'), .chat-operate:has-text('继续沟通')",
    name=None,
)


# --- Success markers (classify after final submit) --------------------------

#: Post-submit success confirmation. Observed only after the final click.
SUCCESS_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=(
        ".chat-operate:has-text('已发送'), .send-status:has-text('已发送'), "
        ".btn-start:has-text('继续沟通')"
    ),
    name=None,
)

#: Post-submit duplicate marker (platform reports a repeat).
SUBMIT_DUPLICATE_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=".error-tip:has-text('已投递'), .tip:has-text('已经投递')",
    name=None,
)

#: Post-submit platform error marker (upload failure / generic platform error).
PLATFORM_ERROR_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=".error-tip, .error-content, .upload-error",
    name=None,
)


#: All required form anchors. The classifier returns ``selector_drift`` if any
#: of these are missing when ``outgoing_text`` is present.
REQUIRED_FORM_ANCHORS = [MESSAGE_INPUT, FINAL_SUBMIT_BUTTON]


__all__ = [
    "CAPTCHA_MARKER",
    "DUPLICATE_MARKER",
    "FINAL_SUBMIT_BUTTON",
    "LOGIN_MARKER",
    "LocatorKind",
    "MESSAGE_INPUT",
    "PLATFORM_ERROR_MARKER",
    "RATE_LIMIT_MARKER",
    "RESUME_UPLOAD",
    "REQUIRED_FORM_ANCHORS",
    "Selector",
    "SUBMIT_DUPLICATE_MARKER",
    "SUCCESS_MARKER",
]
