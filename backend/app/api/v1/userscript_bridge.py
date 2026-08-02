"""Userscript bridge HTTP endpoints.

These endpoints are the communication channel between the Tampermonkey
userscript (running inside the BOSS page) and the backend adapter. They are
**unauthenticated** — the userscript cannot send ``X-User-Id``. Security is
enforced at the instruction/result layer (see
``.trellis/spec/backend/authentication.md`` §Bridge Channel Security).

Four endpoints:

- ``GET /status`` — is a userscript connected?
- ``GET /next-instruction`` — long-poll for the next instruction (5s timeout).
- ``POST /result`` — post back the result of an instruction.
- ``POST /heartbeat`` — keep the connection alive.

The channel is a process-local singleton (see
:func:`app.platforms.boss.userscript_channel.get_channel`). No Redis, no DB.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.logging import get_logger
from app.platforms.boss.sanitizer import sanitize_jd_result
from app.platforms.boss.userscript_channel import (
    InstructionResult,
    get_channel,
    sanitize_result_error,
    sanitize_result_text,
    sanitize_result_url,
)
from app.schemas.userscript_bridge import (
    AckResponse,
    BridgeStatusResponse,
    HeartbeatIn,
    InstructionOut,
    ResultIn,
)

_log = get_logger("app.api.v1.userscript_bridge")

router = APIRouter(prefix="/userscript-bridge", tags=["userscript-bridge"])


@router.get("/status", response_model=BridgeStatusResponse)
def get_status() -> BridgeStatusResponse:
    """Return the current bridge connection status."""
    ch = get_channel()
    page = ch.active_page
    return BridgeStatusResponse(
        connected=ch.is_connected(),
        last_heartbeat=ch.last_heartbeat,
        active_application_id=ch.active_application_id,
        page_id=page.page_id if page else None,
        page_url_hash=page.page_url_hash if page else None,
        page_title=page.page_title if page else None,
    )


@router.get(
    "/next-instruction",
    response_model=InstructionOut,
    responses={204: {"description": "No instruction available"}},
)
async def get_next_instruction(response: Response) -> Response | InstructionOut:
    """Long-poll for the next instruction.

    Returns ``200`` with an :class:`InstructionOut` if an instruction is
    available within 5 seconds, or ``204 No Content`` if the queue is empty.
    """
    ch = get_channel()
    instruction = await ch.take_instruction()
    if instruction is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return response
    return InstructionOut(
        instruction_id=instruction.instruction_id,
        op=instruction.op,
        selector_kind=instruction.selector_kind,
        selector_value=instruction.selector_value,
        selector_name=instruction.selector_name,
        fill_value=instruction.fill_value,
        page_id=instruction.page_id,
        expected_url_hash=instruction.expected_url_hash,
        max_text_chars=instruction.max_text_chars,
        selector_profile=instruction.selector_profile,
    )


@router.post("/result", response_model=AckResponse)
def post_result(body: ResultIn) -> AckResponse:
    """Post back the result of a completed instruction.

    The userscript sends sanitized values only. We apply defense-in-depth
    sanitization here as well (hash URL, strip title, strip error) so even if
    the userscript sends raw values, nothing raw crosses into the channel.

    The ``page_id`` is checked against the pending instruction's ``page_id``.
    A mismatch means a different tab tried to answer — the result is rejected.
    """
    ch = get_channel()
    # Sanitize the JD dict (read_jd results) via defense-in-depth. Each text
    # field is HTML-stripped, secret-stripped, and length-capped. The raw JD
    # dict from the userscript is never stored — only the sanitized version.
    sanitized_jd = sanitize_jd_result(body.jd.model_dump() if body.jd else None)
    accepted = ch.put_result(
        InstructionResult(
            instruction_id=body.instruction_id,
            success=body.success,
            visible=body.visible,
            count=body.count,
            text=sanitize_result_text(body.text),
            url=sanitize_result_url(body.url),
            error=sanitize_result_error(body.error),
            page_id=body.page_id,
            jd=sanitized_jd,
        )
    )
    if not accepted:
        _log.warning(
            "boss.bridge.result_rejected",
            instruction_id=body.instruction_id,
            page_id=body.page_id,
        )
    return AckResponse(ok=True)


@router.post("/heartbeat", response_model=AckResponse)
def post_heartbeat(body: HeartbeatIn) -> AckResponse:
    """Record a heartbeat from the userscript.

    The heartbeat keeps the connection alive and updates the active page
    metadata (``page_id``, ``page_url_hash``, ``page_title``). These are used
    to populate the status endpoint and to bind instructions to the correct
    tab. The page metadata is **not persisted** — it lives only in the
    process-local channel.
    """
    ch = get_channel()
    ch.heartbeat(
        page_id=body.page_id,
        page_url_hash=body.page_url_hash,
        page_title=sanitize_result_text(body.page_title),
    )
    _log.debug(
        "boss.bridge.heartbeat_received",
        page_id=body.page_id,
        page_url_hash=body.page_url_hash,
    )
    return AckResponse(ok=True)
