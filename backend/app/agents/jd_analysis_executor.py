"""Resume-aware JD analysis executor (Phase 3).

The executor builds the chat messages from a verified
:class:`~app.agents.prompts.jd_analysis.JdAnalysisContext`, calls
:class:`~app.models_gateway.base.ModelGateway.chat` (NEVER a provider SDK
directly), parses ``ChatResponse.content`` as JSON, and validates it into
:class:`~app.schemas.jd_analysis.JdAnalysisModelOutput`.

Pydantic validation is the persistence gate: the executor returns a validated
``JdAnalysisModelOutput`` plus provider/model/request/usage metadata, or raises
:class:`JdAnalysisValidationError` on JSON parse or schema failure. Phase 4's
orchestrator translates that exception into the 502
``model returned invalid analysis`` response (design.md §5.1 error table).

The orchestrator drives the workflow as ordered steps (build_prompt_context →
call_model → validate_model_output). ``build_prompt`` and ``call_model`` are
exposed separately so the service can persist timing/metadata for each phase
and pinpoint where a failure occurred.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from app.agents.prompts.jd_analysis import (
    JdAnalysisContext,
    build_jd_analysis_messages,
)
from app.core.logging import get_logger
from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway
from app.schemas.jd_analysis import JdAnalysisModelOutput

_log = get_logger("app.agents.jd_analysis_executor")


class JdAnalysisValidationError(Exception):
    """Raised when the model output cannot be parsed or schema-validated.

    Carries the provider request_id so Phase 4 can include it in the failed
    ``AgentStep`` row and the 502 response. ``kind`` distinguishes a malformed
    JSON body (``"json"``) from a schema-invalid body (``"schema"``).
    """

    def __init__(self, kind: str, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.request_id = request_id


@dataclass
class JdAnalysisExecution:
    """Result of a successful :meth:`JdAnalysisExecutor.execute` call.

    ``output`` is the validated structured analysis — the persistence gate.
    The remaining fields are provider/model/request/usage metadata lifted from
    the :class:`ChatResponse` so Phase 4 can populate ``GeneratedArtifact``
    source_ids, ``model_name``, and ``AgentRun.result`` without re-calling the
    gateway.
    """

    output: JdAnalysisModelOutput
    truncation: dict[str, object]
    provider: str
    model: str
    request_id: str
    latency_ms: int
    usage: dict[str, int | None] | None


class JdAnalysisExecutor:
    """Model-backed executor for the resume-aware JD analysis workflow.

    Stateless aside from its gateway dependency, so a single instance can be
    reused across requests. The executor does not own ``AgentRun`` lifecycle or
    persistence — that is Phase 4's job.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def execute(self, context: JdAnalysisContext) -> JdAnalysisExecution:
        """Run all three model phases and return the validated execution.

        Convenience entry point for callers that do not need per-phase step
        persistence. The service orchestrator calls the individual phase
        methods (``build_prompt`` / ``call_model`` / ``validate``) instead so it
        can record an ``AgentStep`` for each.
        """
        prompt = self.build_prompt(context)
        response = await self.call_model(prompt.messages)
        validated = self.validate(response)
        return JdAnalysisExecution(
            output=validated,
            truncation=prompt.truncation,
            provider=response.provider,
            model=response.model,
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            usage=_usage_to_dict(response.usage),
        )

    def build_prompt(self, context: JdAnalysisContext):
        """Build the chat messages + truncation metadata (build_prompt_context)."""
        return build_jd_analysis_messages(context)

    async def call_model(self, messages) -> ChatResponse:
        """Send the chat request and return the raw response (call_model)."""
        request = ChatRequest(messages=messages, temperature=0.1)
        return await self._gateway.chat(request)

    def validate(self, response: ChatResponse) -> JdAnalysisModelOutput:
        """Parse + schema-validate the model output (validate_model_output).

        Raises :class:`JdAnalysisValidationError` (kind ``"json"`` or
        ``"schema"``) on failure. Phase 4 maps this to the 502
        ``model returned invalid analysis`` response.
        """
        try:
            parsed = json.loads(response.content)
        except (json.JSONDecodeError, TypeError) as exc:
            _log.warning(
                "jd_analysis.invalid_json",
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise JdAnalysisValidationError(
                "json",
                "model returned non-JSON content",
                request_id=response.request_id,
            ) from exc

        try:
            return JdAnalysisModelOutput.model_validate(parsed)
        except ValidationError as exc:
            _log.warning(
                "jd_analysis.schema_validation_failed",
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise JdAnalysisValidationError(
                "schema",
                "model output failed schema validation",
                request_id=response.request_id,
            ) from exc


def _usage_to_dict(usage: object | None) -> dict[str, int | None] | None:
    """Flatten a :class:`~app.models_gateway.base.ChatUsage` into a plain dict.

    Returns ``None`` when the provider did not report usage, matching the
    optional ``ChatResponse.usage`` shape so Phase 4 can store it verbatim in
    ``GeneratedArtifact.source_ids``.
    """
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
