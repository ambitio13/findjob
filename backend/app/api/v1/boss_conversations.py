"""BOSS conversation status scan router (Phase 1 feedback loop).

Exposes ``POST /boss/conversations/scan``: a **read-only** userscript scan of
the BOSS chat list that observes which conversations got a reply / were read.
The result is the semi-automatic input for outcome marking — the frontend can
suggest one-click "已回复" marking from observed status changes.

Safety invariants:

- Authenticated like every user-resource endpoint (unlike the unauthenticated
  userscript bridge data plane, which is guarded by the channel token).
- Read-only on the platform: no clicks, no fills — the adapter sends a single
  ``scan_conversations`` instruction.
- Desensitized result: hashed conversation keys + coarse status flags only.
  Chat text, contact names, and message previews never cross the channel.
- No DB writes: the scan is ephemeral observation (channel is pure memory);
  durable state changes only happen when the user records an outcome.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.platforms.boss.userscript_adapter import UserscriptBossPage
from app.platforms.boss.userscript_channel import get_channel
from app.schemas.boss_conversation_scan import (
    ConversationScanOut,
    ConversationScanStatus,
)
from app.schemas.userscript_bridge import ConversationStatusIn

_log = get_logger("app.api.v1.boss_conversations")

router = APIRouter(prefix="/boss/conversations", tags=["boss-conversations"])


@router.post("/scan", response_model=ConversationScanOut)
async def scan_conversations(
    current_user: UserProfile = Depends(get_current_user),
) -> ConversationScanOut:
    """Scan the BOSS chat list for reply/read statuses (read-only)."""
    channel = get_channel()
    if not channel.is_connected():
        return ConversationScanOut(
            scan_status=ConversationScanStatus.bridge_not_connected,
            message="油猴脚本未连接，请确保已在 BOSS 会话页面安装并运行脚本。",
        )

    page = UserscriptBossPage(channel)
    try:
        entries = await page.scan_conversation_statuses()
    except Exception as exc:  # noqa: BLE001 — scan failure must never 500
        _log.warning(
            "boss_conversations.scan_error",
            user_id=current_user.id,
            error=str(exc),
        )
        entries = None
    finally:
        channel.clear()

    if entries is None:
        return ConversationScanOut(
            scan_status=ConversationScanStatus.scan_failed,
            message="会话扫描失败，请确认当前打开的是 BOSS 沟通（聊天列表）页面。",
        )

    return ConversationScanOut(
        scan_status=ConversationScanStatus.ok,
        conversations=[ConversationStatusIn(**e) for e in entries],
    )
