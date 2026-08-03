"""Pydantic schemas for the userscript bridge HTTP endpoints.

These models define the wire format between the Tampermonkey userscript and the
backend bridge endpoints. Security invariants:

- **No auth header.** The userscript cannot send ``X-User-Id``. Security is
  enforced at the instruction/result layer.
- **Instructions carry only operations + selectors + fill values.** No
  credentials, cookies, or tokens.
- **Results carry only sanitized values.** ``url`` is a sha256 hash, ``text`` is
  a truncated title, ``error`` is diagnostic-stripped.
- **JD results (``read_jd``) are the only raw page text exception.** The ``jd``
  dict carries structured text fields (title, company, description, etc.) —
  never raw HTML. The backend sanitizes each field via ``sanitize_jd_result``
  before storing.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BridgeStatusResponse(BaseModel):
    """Response for ``GET /userscript-bridge/status``."""

    connected: bool = Field(description="Whether a userscript heartbeat is recent.")
    last_heartbeat: datetime | None = None
    active_application_id: str | None = Field(
        default=None,
        description="The application currently being processed, if any.",
    )
    page_id: str | None = Field(
        default=None,
        description="Stable identifier of the connected browser tab, if any.",
    )
    page_url_hash: str | None = Field(
        default=None,
        description="sha256 hash of the connected page URL, if any. Never the raw URL.",
    )
    page_title: str | None = Field(
        default=None,
        description="Sanitized title of the connected page, if any.",
    )


class InstructionOut(BaseModel):
    """One instruction sent to the userscript via ``GET /next-instruction``."""

    instruction_id: str = Field(min_length=1)
    op: str = Field(
        description=(
            "Operation: fill, click, check_visible, count, "
            "read_title, read_url, read_content, read_jd, "
            "click_immediate_communicate, fill_opening_message, "
            "send_opening_message, read_communication_result, "
            "probe_elements."
        )
    )
    selector_kind: str | None = Field(
        default=None, description="role, label, placeholder, or css."
    )
    selector_value: str | None = None
    selector_name: str | None = Field(
        default=None, description="Accessible name for role selectors."
    )
    fill_value: str | None = Field(
        default=None,
        description="Value to type, only for the fill and fill_opening_message ops.",
    )
    page_id: str | None = Field(
        default=None,
        description=(
            "The browser tab that should execute this instruction. The "
            "userscript must refuse if it does not match its own page_id."
        ),
    )
    expected_url_hash: str | None = Field(
        default=None,
        description=(
            "sha256 hash of the page URL expected when this instruction "
            "executes. The userscript must refuse if its current URL hash "
            "does not match."
        ),
    )
    max_text_chars: int | None = Field(
        default=None,
        description=(
            "Maximum total text chars for read_jd results. The userscript "
            "truncates JD fields to fit within this budget."
        ),
    )
    selector_profile: str | None = Field(
        default=None,
        description=(
            "Extraction profile for read_jd. Tells the userscript which DOM "
            "structure to extract from (e.g. boss_recommended_job_v1)."
        ),
    )


class JDResultIn(BaseModel):
    """Structured JD data returned by the ``read_jd`` op.

    All text fields are optional; the backend checks minimum required fields
    (title + description) after sanitization and flags ``jd_too_sparse`` if
    they are missing.

    ``page_url_hash`` is a sha256 hash computed by the userscript — never a
    raw URL. ``source_kind`` identifies the extraction profile used.
    """

    title: str | None = None
    company: str | None = None
    location: str | None = None
    salary: str | None = None
    experience: str | None = None
    education: str | None = None
    skills: list[str] | None = None
    description: str | None = None
    source_kind: str | None = None
    page_url_hash: str | None = None


class ResultIn(BaseModel):
    """Result posted back by the userscript via ``POST /result``."""

    instruction_id: str = Field(min_length=1)
    success: bool = True
    visible: bool | None = None
    count: int | None = None
    text: str | None = Field(
        default=None,
        description="Sanitized text (title/content), already stripped by the userscript.",
    )
    url: str | None = Field(
        default=None,
        description="sha256 hash of the page URL, computed by the userscript.",
    )
    error: str | None = Field(
        default=None, description="Short diagnostic-stripped error message."
    )
    page_id: str | None = Field(
        default=None,
        description=(
            "The page_id of the tab that produced this result. Must match "
            "the instruction's page_id or the result is rejected."
        ),
    )
    jd: JDResultIn | None = Field(
        default=None,
        description=(
            "Structured JD data, only for read_jd results. Text fields only "
            "— never raw HTML. Sanitized by the backend before storage."
        ),
    )
    marker_counts: dict[str, int] | None = Field(
        default=None,
        description=(
            "Raw element counts for success / duplicate / error markers, "
            "only for read_communication_result results. The backend "
            "classifies the outcome — the userscript does NOT classify."
        ),
    )


class HeartbeatIn(BaseModel):
    """Heartbeat posted by the userscript via ``POST /heartbeat``."""

    page_id: str | None = Field(
        default=None,
        description="Stable per-tab identifier generated by the userscript.",
    )
    page_url_hash: str | None = Field(
        default=None, description="sha256 hash of the current page URL."
    )
    page_title: str | None = Field(
        default=None, description="Sanitized page title (truncated by the userscript)."
    )


class AckResponse(BaseModel):
    """Simple acknowledgement response."""

    ok: bool = True


__all__ = [
    "AckResponse",
    "BridgeStatusResponse",
    "HeartbeatIn",
    "InstructionOut",
    "JDResultIn",
    "ResultIn",
]
