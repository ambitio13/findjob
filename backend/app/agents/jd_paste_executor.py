"""JD paste parsing executor.

The executor builds the chat messages from raw JD text and an optional platform
hint, calls :class:`~app.models_gateway.base.ModelGateway.chat` (NEVER a
provider SDK directly), parses ``ChatResponse.content`` as JSON, and validates
it into :class:`~app.schemas.jd_paste_facts.JdPasteFactsModelOutput`.

Pydantic validation is the persistence gate: the executor returns a validated
``JdPasteFactsModelOutput`` plus provider/model/request/usage metadata, or
raises :class:`JdPasteValidationError` on JSON parse or schema failure. The
orchestrator translates that exception into a persisted failed run and a 200
response with empty typed fields (parsing is an auxiliary preview action).

The orchestrator drives the workflow as ordered steps (build_prompt_context →
call_model → validate_model_output). ``build_prompt`` and ``call_model`` are
exposed separately so the service can persist timing/metadata for each phase
and pinpoint where a failure occurred. This mirrors
:class:`~app.agents.resume_fact_executor.ResumeFactExecutor`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from app.agents.prompts.jd_paste import build_jd_parse_messages
from app.core.logging import get_logger
from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway
from app.schemas.jd_paste_facts import JdPasteFactsModelOutput

_log = get_logger("app.agents.jd_paste_executor")


class JdPasteValidationError(Exception):
    """Raised when the model output cannot be parsed or schema-validated.

    Carries the provider request_id so the service can include it in the failed
    ``AgentStep`` row. ``kind`` distinguishes a malformed JSON body (``"json"``)
    from a schema-invalid body (``"schema"``).
    """

    def __init__(self, kind: str, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.request_id = request_id


@dataclass
class JdPasteExecution:
    """Result of a successful parse.

    ``output`` is the validated structured draft fields — the persistence gate.
    The remaining fields are provider/model/request/usage metadata lifted from
    the :class:`ChatResponse` so the service can populate ``AgentRun.result``
    without re-calling the gateway.
    """

    output: JdPasteFactsModelOutput
    truncation: dict[str, object]
    provider: str
    model: str
    request_id: str
    latency_ms: int
    usage: dict[str, int | None] | None


class JdPasteExecutor:
    """Model-backed executor for the JD paste parsing workflow.

    Stateless aside from its gateway dependency, so a single instance can be
    reused across requests. The executor does not own ``AgentRun`` lifecycle or
    persistence — that is the service's job.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def execute(self, raw_jd: str, platform_hint: str | None = None) -> JdPasteExecution:
        """Run all three model phases and return the validated execution.

        Convenience entry point for callers that do not need per-phase step
        persistence. The service orchestrator calls the individual phase
        methods instead so it can record an ``AgentStep`` for each.
        """
        prompt = self.build_prompt(raw_jd, platform_hint)
        response = await self.call_model(prompt.messages)
        validated = self.validate(response)
        return JdPasteExecution(
            output=validated,
            truncation=prompt.truncation,
            provider=response.provider,
            model=response.model,
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            usage=_usage_to_dict(response.usage),
        )

    def build_prompt(self, raw_jd: str, platform_hint: str | None = None):
        """Build the chat messages + truncation metadata (build_prompt_context)."""
        return build_jd_parse_messages(raw_jd, platform_hint)

    async def call_model(self, messages) -> ChatResponse:
        """Send the chat request and return the raw response (call_model)."""
        request = ChatRequest(messages=messages, temperature=0.1)
        return await self._gateway.chat(request)

    def validate(self, response: ChatResponse) -> JdPasteFactsModelOutput:
        """Parse + schema-validate the model output (validate_model_output).

        Raises :class:`JdPasteValidationError` (kind ``"json"`` or ``"schema"``)
        on failure. The service maps this to a persisted failed run and a 200
        response with empty typed fields.
        """
        try:
            parsed = json.loads(response.content)
        except (json.JSONDecodeError, TypeError) as exc:
            _log.warning(
                "jd_paste.invalid_json",
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise JdPasteValidationError(
                "json",
                "model returned non-JSON content",
                request_id=response.request_id,
            ) from exc

        try:
            return JdPasteFactsModelOutput.model_validate(parsed)
        except ValidationError as exc:
            _log.warning(
                "jd_paste.schema_validation_failed",
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise JdPasteValidationError(
                "schema",
                "model output failed schema validation",
                request_id=response.request_id,
            ) from exc


def _usage_to_dict(usage: object | None) -> dict[str, int | None] | None:
    """Flatten a :class:`~app.models_gateway.base.ChatUsage` into a plain dict.

    Returns ``None`` when the provider did not report usage, matching the
    optional ``ChatResponse.usage`` shape so the service can store it verbatim.
    """
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
