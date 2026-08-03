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


# --- Immediate-communicate selectors ----------------------------------------
#
# These are distinct from the resume-submission selectors above. The
# communicate flow clicks "立即沟通" on the job card page, which opens a chat
# dialog with its own message input and send button. The markers below are
# checked after the send click to classify the communication result.

#: The "立即沟通" button on the job card / job detail page. Clicking this opens
#: the chat dialog. Clicked at most once per communicate execute.
IMMEDIATE_COMMUNICATE_BUTTON = Selector(
    kind=LocatorKind.ROLE,
    value="button",
    name="立即沟通",
)

#: The "继续沟通" button — shown when a conversation already exists with this
#: HR. Clicking it also opens the chat dialog. The adapter falls back to this
#: when 立即沟通 is absent (e.g. a previous execute clicked 立即沟通 but failed
#: before sending the message).
CONTINUE_COMMUNICATE_BUTTON = Selector(
    kind=LocatorKind.ROLE,
    value="button",
    name="继续沟通",
)

#: The chat message input inside the communicate dialog. Distinct from
#: ``MESSAGE_INPUT`` (the resume-submission HR message input) — the chat
#: dialog uses a different DOM structure. This selector is intentionally broad:
#: it covers the known BOSS chat dialog variants (contenteditable div, textarea,
#: and input) across markup changes. The userscript's ``fill_opening_message``
#: picks the first visible match, and the chat dialog is the only editable
#: element on the page after clicking "立即沟通".
COMMUNICATION_MESSAGE_INPUT = Selector(
    kind=LocatorKind.CSS,
    value=(
        # .edit-area textarea — confirmed on real BOSS job detail page (2026-08-03).
        # BOSS wraps the chat <textarea> in a div.edit-area; no contenteditable attr.
        ".edit-area textarea,"
        # contenteditable div variants (older BOSS markup)
        ".chat-input [contenteditable='true'],"
        " .chat-footer [contenteditable='true'],"
        " .chat-box [contenteditable='true'],"
        " .input-wrap [contenteditable='true'],"
        " .edit-area[contenteditable='true'],"
        # textarea variants in other chat containers
        ".chat-input textarea,"
        " .chat-box textarea,"
        " .input-wrap textarea,"
        " .chat-message textarea,"
        # input variants (older)
        ".chat-message input[type='text'],"
        ".chat-input input[type='text'],"
        # broad fallback: any visible contenteditable or textarea in a chat container
        ".chat-content [contenteditable='true'],"
        " [class*='chat'] [contenteditable='true'],"
        " [class*='chat'] textarea"
    ),
    name=None,
)

#: The send button inside the communicate dialog. Clicked at most once per
#: communicate execute. BOSS chat send button is typically a <div> or <span>
#: with a class containing "send" inside the chat panel — NOT a standard
#: <button>. We use a CSS selector targeting the chat area. The userscript's
#: ``send_opening_message`` handler also has an Enter-key fallback if no
#: element matches.
COMMUNICATION_SEND_BUTTON = Selector(
    kind=LocatorKind.CSS,
    value=(
        # BOSS chat send button variants (icon/text div or span)
        ".chat-message .btn-send,"
        ".chat-message [class*='send'],"
        ".chat-input .btn-send,"
        ".chat-input [class*='send'],"
        ".chat-content .btn-send,"
        ".chat-content [class*='send'],"
        ".edit-area .btn-send,"
        ".edit-area [class*='send'],"
        # broader: any clickable element with "发送" text inside chat containers
        ".chat-message [class*='btn']:has-text('发送'),"
        ".chat-input [class*='btn']:has-text('发送'),"
        ".chat-content [class*='btn']:has-text('发送'),"
        # final fallback: any element with class containing send in a chat context
        " [class*='chat'] [class*='send']"
    ),
    name="发送",
)

#: Post-send success marker. After ``send_opening_message``, the chat dialog
#: shows a sent confirmation ("已发送") or a non-pending message item. Note:
#: "继续沟通" is a *duplicate* marker (see ``COMMUNICATION_DUPLICATE_MARKER``),
#: not a success marker — it means a conversation already existed.
COMMUNICATION_SUCCESS_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=(
        ".chat-message:has-text('已发送'), .message-status:has-text('已发送'), "
        ".chat-content .message-item:not(.pending), "
        ".chat-message:has-text('发送成功'), "
        "[class*='message']:has-text('已发送')"
    ),
    name=None,
)

#: Post-send duplicate marker. Indicates a conversation already existed for
#: this contact (e.g. "继续沟通" button is shown instead of a fresh send).
COMMUNICATION_DUPLICATE_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=(
        ".btn-start:has-text('继续沟通'), .chat-operate:has-text('继续沟通'), "
        "[class*='btn']:has-text('继续沟通'), "
        "[class*='operate']:has-text('继续沟通')"
    ),
    name=None,
)


__all__ = [
    "CAPTCHA_MARKER",
    "COMMUNICATION_DUPLICATE_MARKER",
    "COMMUNICATION_MESSAGE_INPUT",
    "COMMUNICATION_SEND_BUTTON",
    "COMMUNICATION_SUCCESS_MARKER",
    "CONTINUE_COMMUNICATE_BUTTON",
    "DUPLICATE_MARKER",
    "FINAL_SUBMIT_BUTTON",
    "IMMEDIATE_COMMUNICATE_BUTTON",
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
