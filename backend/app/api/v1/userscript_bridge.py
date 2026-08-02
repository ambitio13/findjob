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
    return BridgeStatusResponse(
        connected=ch.is_connected(),
        last_heartbeat=ch.last_heartbeat,
        active_application_id=ch.active_application_id,
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
    )


@router.post("/result", response_model=AckResponse)
def post_result(body: ResultIn) -> AckResponse:
    """Post back the result of a completed instruction.

    The userscript sends sanitized values only. We apply defense-in-depth
    sanitization here as well (hash URL, strip title, strip error) so even if
    the userscript sends raw values, nothing raw crosses into the channel.
    """
    ch = get_channel()
    ch.put_result(
        InstructionResult(
            instruction_id=body.instruction_id,
            success=body.success,
            visible=body.visible,
            count=body.count,
            text=sanitize_result_text(body.text),
            url=sanitize_result_url(body.url),
            error=sanitize_result_error(body.error),
        )
    )
    return AckResponse(ok=True)


@router.post("/heartbeat", response_model=AckResponse)
def post_heartbeat(body: HeartbeatIn) -> AckResponse:
    """Record a heartbeat from the userscript.

    The heartbeat keeps the connection alive. ``page_url_hash`` and
    ``page_title`` are accepted for observability but are **not persisted** —
    they are only used to update the channel's last-heartbeat timestamp.
    """
    ch = get_channel()
    ch.heartbeat()
    _log.debug(
        "boss.bridge.heartbeat_received",
        page_url_hash=body.page_url_hash,
    )
    return AckResponse(ok=True)
