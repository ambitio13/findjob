"""Resume fact extraction executor.

The executor builds the chat messages from raw resume text and filename, calls
:class:`~app.models_gateway.base.ModelGateway.chat` (NEVER a provider SDK
directly), parses ``ChatResponse.content`` as JSON, and validates it into
:class:`~app.schemas.resume_facts.ResumeFactsModelOutput`.

Pydantic validation is the persistence gate: the executor returns a validated
``ResumeFactsModelOutput`` plus provider/model/request/usage metadata, or raises
:class:`ResumeFactValidationError` on JSON parse or schema failure. The
orchestrator translates that exception into the 502
``model returned invalid resume facts`` response (for the explicit re-extract
endpoint), or a persisted failed run (for the upload path).

The orchestrator drives the workflow as ordered steps (build_prompt_context →
call_model → validate_model_output). ``build_prompt`` and ``call_model`` are
exposed separately so the service can persist timing/metadata for each phase
and pinpoint where a failure occurred. This mirrors
:class:`~app.agents.jd_analysis_executor.JdAnalysisExecutor`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from app.agents.prompts.resume_fact import (
    build_resume_fact_messages,
)
from app.core.logging import get_logger
from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway
from app.schemas.resume_facts import ResumeFactsModelOutput

_log = get_logger("app.agents.resume_fact_executor")


class ResumeFactValidationError(Exception):
    """Raised when the model output cannot be parsed or schema-validated.

    Carries the provider request_id so the service can include it in the failed
    ``AgentStep`` row and the 502 response. ``kind`` distinguishes a malformed
    JSON body (``"json"``) from a schema-invalid body (``"schema"``).
    """

    def __init__(self, kind: str, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.request_id = request_id


@dataclass
class ResumeFactExecution:
    """Result of a successful extraction.

    ``output`` is the validated structured facts — the persistence gate. The
    remaining fields are provider/model/request/usage metadata lifted from the
    :class:`ChatResponse` so the service can populate ``parsed_facts._extraction``
    and ``AgentRun.result`` without re-calling the gateway.
    """

    output: ResumeFactsModelOutput
    truncation: dict[str, object]
    provider: str
    model: str
    request_id: str
    latency_ms: int
    usage: dict[str, int | None] | None


class ResumeFactExecutor:
    """Model-backed executor for the resume fact extraction workflow.

    Stateless aside from its gateway dependency, so a single instance can be
    reused across requests. The executor does not own ``AgentRun`` lifecycle or
    persistence — that is the service's job.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def execute(self, raw_text: str, filename: str) -> ResumeFactExecution:
        """Run all three model phases and return the validated execution.

        Convenience entry point for callers that do not need per-phase step
        persistence. The service orchestrator calls the individual phase
        methods instead so it can record an ``AgentStep`` for each.
        """
        prompt = self.build_prompt(raw_text, filename)
        response = await self.call_model(prompt.messages)
        validated = self.validate(response)
        return ResumeFactExecution(
            output=validated,
            truncation=prompt.truncation,
            provider=response.provider,
            model=response.model,
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            usage=_usage_to_dict(response.usage),
        )

    def build_prompt(self, raw_text: str, filename: str):
        """Build the chat messages + truncation metadata (build_prompt_context)."""
        return build_resume_fact_messages(raw_text, filename)

    async def call_model(self, messages) -> ChatResponse:
        """Send the chat request and return the raw response (call_model)."""
        request = ChatRequest(messages=messages, temperature=0.1)
        return await self._gateway.chat(request)

    def validate(self, response: ChatResponse) -> ResumeFactsModelOutput:
        """Parse + schema-validate the model output (validate_model_output).

        Raises :class:`ResumeFactValidationError` (kind ``"json"`` or
        ``"schema"``) on failure. The service maps this to the 502
        ``model returned invalid resume facts`` response (re-extract) or a
        persisted failed run (upload).
        """
        try:
            parsed = json.loads(response.content)
        except (json.JSONDecodeError, TypeError) as exc:
            _log.warning(
                "resume_fact.invalid_json",
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise ResumeFactValidationError(
                "json",
                "model returned non-JSON content",
                request_id=response.request_id,
            ) from exc

        try:
            return ResumeFactsModelOutput.model_validate(parsed)
        except ValidationError as exc:
            _log.warning(
                "resume_fact.schema_validation_failed",
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise ResumeFactValidationError(
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
