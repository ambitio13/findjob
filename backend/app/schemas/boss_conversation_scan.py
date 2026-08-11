"""Pydantic schemas for the BOSS conversation status scan (Phase 1).

The scan is a **read-only** userscript operation: it observes reply/read
statuses on the BOSS chat list page and returns desensitized entries (hashed
conversation keys + coarse status flags). Chat text never crosses the
channel — this keeps the existing privacy invariants intact while feeding
the outcome feedback loop.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.userscript_bridge import ConversationStatusIn


class ConversationScanStatus(StrEnum):
    ok = "ok"
    bridge_not_connected = "bridge_not_connected"
    scan_failed = "scan_failed"


class ConversationScanOut(BaseModel):
    """Response for ``POST /boss/conversations/scan``."""

    scan_status: ConversationScanStatus
    message: str | None = None
    #: Desensitized entries only — hashed keys + statuses, never chat text.
    conversations: list[ConversationStatusIn] = Field(default_factory=list)
