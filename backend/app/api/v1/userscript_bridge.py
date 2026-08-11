"""Userscript bridge HTTP endpoints.

These endpoints are the communication channel between the Tampermonkey
userscript (running inside the BOSS page) and the backend adapter. They carry
no *user* authentication — the userscript cannot send ``X-User-Id``. Security
is enforced at two layers instead (see
``.trellis/spec/backend/authentication.md`` §Bridge Channel Security):

- **Channel token** (Phase 0): when ``BOSS_BRIDGE_CHANNEL_TOKEN`` is set,
  every data-plane endpoint (``next-instruction`` / ``result`` / ``heartbeat``
  / ``probe``) requires a matching ``X-Bridge-Token`` header. The userscript
  reads the token from its own config block, so it never crosses the channel
  in a request body. In prod a missing token refuses the data plane outright
  (503). ``GET /status`` stays open: the frontend needs it for the connection
  indicator and it exposes only connection metadata, never instruction or
  result payloads.
- **Instruction/result sanitization**: operations-only instructions,
  sanitized results, in-memory queue.

Four endpoints:

- ``GET /status`` — is a userscript connected?
- ``GET /next-instruction`` — long-poll for the next instruction (5s timeout).
- ``POST /result`` — post back the result of an instruction.
- ``POST /heartbeat`` — keep the connection alive.

The channel is a process-local singleton (see
:func:`app.platforms.boss.userscript_channel.get_channel`). No Redis, no DB.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.platforms.boss.sanitizer import (
    _COOKIE_PATTERN,
    _SECRET_PATTERNS,
    sanitize_jd_result,
)
from app.platforms.boss.userscript_channel import (
    InstructionResult,
    get_channel,
    make_instruction,
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


def require_bridge_token(
    x_bridge_token: str | None = Header(alias="X-Bridge-Token", default=None),
) -> None:
    """Guard the bridge data plane with the shared channel token.

    Rules:

    - ``BOSS_BRIDGE_CHANNEL_TOKEN`` set → the request header must match
      (constant-time compare), otherwise 401.
    - Token unset in ``prod`` → the data plane is refused entirely (503);
      an open bridge in prod is never acceptable.
    - Token unset in ``local``/``test`` → legacy open behaviour, so existing
      userscripts and tests keep working unchanged.
    """
    settings = get_settings()
    expected = settings.boss_bridge_channel_token
    if not expected:
        if settings.app_env == "prod":
            _log.error("boss.bridge.data_plane_refused", reason="channel_token_not_configured")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"reason": "bridge_channel_token_not_configured"},
            )
        return
    if not x_bridge_token or not hmac.compare_digest(x_bridge_token, expected):
        _log.warning("boss.bridge.token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "bridge_token_invalid"},
        )


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
    dependencies=[Depends(require_bridge_token)],
)
async def get_next_instruction(
    response: Response,
    page_id: str | None = None,
) -> Response | InstructionOut:
    """Long-poll for the next instruction.

    Returns ``200`` with an :class:`InstructionOut` if an instruction is
    available within 5 seconds, or ``204 No Content`` if the queue is empty.

    The optional *page_id* query parameter filters the queue so that only
    instructions bound to this tab (or unbound instructions) are returned.
    Non-matching instructions are re-enqueued for the correct tab. This is
    the server-side half of page binding — the userscript also checks
    ``page_id`` client-side as defense-in-depth.
    """
    ch = get_channel()
    instruction = await ch.take_instruction_for_page(page_id)
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
        extra_selectors=instruction.extra_selectors,
    )


@router.post("/result", response_model=AckResponse, dependencies=[Depends(require_bridge_token)])
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
    # text field: sanitize normally, EXCEPT for probe_elements which returns
    # a JSON diagnostic payload (up to 8000 chars) that should not be truncated
    # to _TITLE_MAX (120). The probe endpoint is P0-2 verification only.
    raw_text = body.text
    if raw_text and len(raw_text) > 120 and raw_text.lstrip().startswith("["):
        # Likely a probe_elements JSON payload — sanitize secrets but don't truncate.
        cleaned_text = raw_text.strip()
        for pattern in _SECRET_PATTERNS:
            cleaned_text = pattern.sub(r"\1<redacted>", cleaned_text)
        cleaned_text = _COOKIE_PATTERN.sub(r"\1=<redacted>", cleaned_text)
        sanitized_text = cleaned_text[:8000] if cleaned_text else None
    else:
        sanitized_text = sanitize_result_text(raw_text)
    accepted = ch.put_result(
        InstructionResult(
            instruction_id=body.instruction_id,
            success=body.success,
            visible=body.visible,
            count=body.count,
            text=sanitized_text,
            url=sanitize_result_url(body.url),
            error=sanitize_result_error(body.error),
            page_id=body.page_id,
            jd=sanitized_jd,
            marker_counts=body.marker_counts,
            # scan_conversations results: already desensitized by the schema
            # (hashed keys + coarse status flags, capped at 50 entries).
            conversations=[c.model_dump() for c in body.conversations]
            if body.conversations
            else None,
        )
    )
    if not accepted:
        _log.warning(
            "boss.bridge.result_rejected",
            instruction_id=body.instruction_id,
            page_id=body.page_id,
        )
    return AckResponse(ok=True)


@router.post(
    "/heartbeat", response_model=AckResponse, dependencies=[Depends(require_bridge_token)]
)
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


# ---------------------------------------------------------------------------
# Temporary diagnostic endpoint — probe DOM selectors (P0-2 verification)
# ---------------------------------------------------------------------------


class ProbeRequest(BaseModel):
    """Request body for the diagnostic probe endpoint."""

    selector_kind: str = Field(default="css", description="css, role, placeholder")
    selector_value: str = Field(default="", description="The selector value to probe.")
    selector_name: str | None = Field(default=None)
    op: str = Field(
        default="count",
        description=(
            "count, check_visible, read_content, read_title, read_url, "
            "read_jd, probe_elements."
        ),
    )
    selector_profile: str | None = Field(
        default=None,
        description="Extraction profile for read_jd (e.g. boss_recommended_job_v1).",
    )
    max_text_chars: int | None = Field(
        default=None,
        description="Max chars to extract for read_jd.",
    )
    page_id: str | None = Field(
        default=None,
        description=(
            "Bind the instruction to this page_id (tab). When omitted, the "
            "instruction auto-binds to the active page. Used by Stage 7 "
            "multi-tab verification to test page_id filtering."
        ),
    )
    expected_url_hash: str | None = Field(
        default=None,
        description=(
            "Expected page URL hash. When omitted, auto-binds from the active "
            "page. Used with page_id for Stage 7 multi-tab verification."
        ),
    )


class ProbeResponse(BaseModel):
    """Response from the diagnostic probe endpoint."""

    success: bool = True
    count: int | None = None
    visible: bool | None = None
    text: str | None = None
    url: str | None = None
    error: str | None = None
    jd: dict | None = None
    instruction_page_id: str | None = None


#: Probe is a selector-debugging surface, not a product feature. To keep the
#: data plane minimal it is disabled entirely in prod and restricted to
#: non-content-extracting operations everywhere (``read_content`` can read
#: arbitrary page body text and is never allowed through probe).
_PROBE_ALLOWED_OPS = frozenset(
    {"count", "check_visible", "read_title", "read_url", "read_jd", "probe_elements"}
)


@router.post("/probe", response_model=ProbeResponse, dependencies=[Depends(require_bridge_token)])
async def probe(body: ProbeRequest) -> ProbeResponse:
    """Send a diagnostic instruction to the userscript and return the raw result.

    This endpoint is for selector verification only — it lets us probe the
    real BOSS DOM to find the correct CSS selectors. It is disabled in prod
    and limited to a non-content op whitelist (see
    :data:`_PROBE_ALLOWED_OPS`).

    When *page_id* is provided, the instruction is bound to that specific tab.
    This is the Stage 7 multi-tab safety test: we send an instruction bound to
    Tab-A's page_id while Tab-B is the active page. The userscript on Tab-A
    should pick it up; Tab-B should skip it.
    """
    settings = get_settings()
    if settings.app_env == "prod":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"reason": "probe_disabled_in_prod"},
        )
    if body.op not in _PROBE_ALLOWED_OPS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "probe_op_not_allowed", "op": body.op},
        )

    ch = get_channel()
    if not ch.is_connected():
        return ProbeResponse(success=False, error="bridge_not_connected")

    ins = make_instruction(
        op=body.op,  # type: ignore[arg-type]
        selector_kind=body.selector_kind,
        selector_value=body.selector_value,
        selector_name=body.selector_name,
        selector_profile=body.selector_profile,
        max_text_chars=body.max_text_chars,
        page_id=body.page_id,
        expected_url_hash=body.expected_url_hash,
    )
    result = await ch.put_instruction(ins)
    return ProbeResponse(
        success=result.success,
        count=result.count,
        visible=result.visible,
        text=result.text,
        url=result.url,
        error=result.error,
        jd=result.jd,
        instruction_page_id=ins.page_id,
    )
