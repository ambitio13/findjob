"""Readiness artifact generation executor.

The executor builds the chat messages from a verified
:class:`~app.agents.prompts.readiness.ReadinessContext`, calls
:class:`~app.models_gateway.base.ModelGateway.chat` (NEVER a provider SDK
directly), parses ``ChatResponse.content`` as JSON, and validates it into the
artifact-type-specific output model.

Pydantic validation is the persistence gate: the executor returns a validated
output model plus provider/model/request/usage metadata, or raises
:class:`ReadinessValidationError` on JSON parse or schema failure. The service
orchestrator translates that exception into a failed ``AgentRun`` + ``AgentStep``
(design.md §Failure Persistence).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.agents.prompts.readiness import (
    ReadinessContext,
    build_readiness_messages,
)
from app.core.logging import get_logger
from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway
from app.schemas.readiness import (
    HrOpeningMessageOutput,
    InterviewPrepOutput,
    ReadinessArtifactType,
    ResumeRewriteSnippetOutput,
    SkillGapPlanOutput,
)

_log = get_logger("app.agents.readiness_executor")

#: Maps artifact type to the Pydantic model that validates the model output.
_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    ReadinessArtifactType.hr_opening_message.value: HrOpeningMessageOutput,
    ReadinessArtifactType.resume_rewrite_snippet.value: ResumeRewriteSnippetOutput,
    ReadinessArtifactType.skill_gap_plan.value: SkillGapPlanOutput,
    ReadinessArtifactType.interview_prep.value: InterviewPrepOutput,
}


class ReadinessValidationError(Exception):
    """Raised when the model output cannot be parsed or schema-validated.

    Carries the provider request_id so the orchestrator can include it in the
    failed ``AgentStep`` row. ``kind`` distinguishes a malformed JSON body
    (``"json"``) from a schema-invalid body (``"schema"``).
    """

    def __init__(self, kind: str, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.request_id = request_id


@dataclass
class ReadinessExecution:
    """Result of a successful executor call.

    ``output`` is the validated structured artifact — the persistence gate.
    The remaining fields are provider/model/request/usage metadata lifted from
    the :class:`ChatResponse` so the orchestrator can populate
    ``GeneratedArtifact`` source_ids, ``model_name``, and ``AgentRun.result``
    without re-calling the gateway.
    """

    output: BaseModel
    truncation: dict[str, object]
    provider: str
    model: str
    request_id: str
    latency_ms: int
    usage: dict[str, int | None] | None


class ReadinessExecutor:
    """Model-backed executor for the readiness artifact generation workflow.

    Stateless aside from its gateway dependency, so a single instance can be
    reused across requests. The executor does not own ``AgentRun`` lifecycle or
    persistence — that is the service's job.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    def build_prompt(self, context: ReadinessContext):
        """Build the chat messages + truncation metadata (build_prompt_context)."""
        return build_readiness_messages(context)

    async def call_model(self, messages) -> ChatResponse:
        """Send the chat request and return the raw response (call_model)."""
        request = ChatRequest(messages=messages, temperature=0.1)
        return await self._gateway.chat(request)

    def validate(self, response: ChatResponse, artifact_type: str) -> BaseModel:
        """Parse + schema-validate the model output (validate_model_output).

        Raises :class:`ReadinessValidationError` (kind ``"json"`` or
        ``"schema"``) on failure.
        """
        try:
            parsed = json.loads(response.content)
        except (json.JSONDecodeError, TypeError) as exc:
            _log.warning(
                "readiness.invalid_json",
                artifact_type=artifact_type,
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise ReadinessValidationError(
                "json",
                "model returned non-JSON content",
                request_id=response.request_id,
            ) from exc

        model_cls = _OUTPUT_MODELS.get(artifact_type)
        if model_cls is None:
            raise ReadinessValidationError(
                "schema",
                f"unknown artifact type: {artifact_type}",
                request_id=response.request_id,
            )

        try:
            return model_cls.model_validate(parsed)
        except ValidationError as exc:
            _log.warning(
                "readiness.schema_validation_failed",
                artifact_type=artifact_type,
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise ReadinessValidationError(
                "schema",
                "model output failed schema validation",
                request_id=response.request_id,
            ) from exc


def usage_to_dict(usage: object | None) -> dict[str, int | None] | None:
    """Flatten a :class:`~app.models_gateway.base.ChatUsage` into a plain dict.

    Returns ``None`` when the provider did not report usage, matching the
    optional ``ChatResponse.usage`` shape so the orchestrator can store it
    verbatim in ``GeneratedArtifact.source_ids``.
    """
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def failed_execution() -> ReadinessExecution:
    """Return a placeholder execution for the worker (no-output) failure path."""
    return ReadinessExecution(
        output=HrOpeningMessageOutput(hook="", message=""),  # type: ignore[arg-type]
        truncation={},
        provider="",
        model="",
        request_id="",
        latency_ms=0,
        usage=None,
    )


def serialize_output(output: BaseModel) -> str:
    """Serialize a validated output model to JSON for ``GeneratedArtifact.content``."""
    return output.model_dump_json()
