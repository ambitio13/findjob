"""BOSS match-decision executor.

The executor builds the chat messages from a verified
:class:`~app.agents.prompts.boss_match.BossMatchContext`, calls
:class:`~app.models_gateway.base.ModelGateway.chat` (NEVER a provider SDK
directly), parses ``ChatResponse.content`` as JSON, and validates it into
:class:`~app.schemas.boss_match_decision.MatchDecisionModelOutput`.

Pydantic validation is the persistence gate: the executor returns a validated
``MatchDecisionModelOutput`` plus provider/model/request/usage metadata, or
raises :class:`BossMatchValidationError` on JSON parse or schema failure. The
service translates that exception into the 502
``model returned invalid match decision`` response (design.md §5.1 error table).

The orchestrator drives the workflow as ordered steps (build_prompt_context →
call_model → validate_model_output). ``build_prompt`` and ``call_model`` are
exposed separately so the service can persist timing/metadata for each phase
and pinpoint where a failure occurred.

After validation the service applies
:func:`~app.agents.opening_message_guard.apply_match_safety_gate` to enforce
deterministic backend safety rules (low-confidence downgrade, opening-message
PII/length checks). That guard lives outside the executor so it can be unit-
tested independently and so the executor stays a pure model-output validator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from app.agents.jd_analysis_executor import _usage_to_dict
from app.agents.prompts.boss_match import (
    BossMatchContext,
    build_boss_match_messages,
)
from app.core.logging import get_logger
from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway
from app.schemas.boss_match_decision import MatchDecisionModelOutput

_log = get_logger("app.agents.boss_match_executor")


class BossMatchValidationError(Exception):
    """Raised when the model output cannot be parsed or schema-validated.

    Carries the provider request_id so the service can include it in the
    failed ``AgentStep`` row and the 502 response. ``kind`` distinguishes a
    malformed JSON body (``"json"``) from a schema-invalid body (``"schema"``).
    """

    def __init__(self, kind: str, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.request_id = request_id


@dataclass
class BossMatchExecution:
    """Result of a successful :meth:`BossMatchExecutor.execute` call.

    ``output`` is the validated structured match decision — the persistence
    gate. The remaining fields are provider/model/request/usage metadata lifted
    from the :class:`ChatResponse` so the service can populate
    ``GeneratedArtifact`` source_ids, ``model_name``, and ``AgentRun.result``
    without re-calling the gateway.
    """

    output: MatchDecisionModelOutput
    truncation: dict[str, object]
    provider: str
    model: str
    request_id: str
    latency_ms: int
    usage: dict[str, int | None] | None


class BossMatchExecutor:
    """Model-backed executor for the BOSS match-decision workflow.

    Stateless aside from its gateway dependency, so a single instance can be
    reused across requests. The executor does not own ``AgentRun`` lifecycle or
    persistence — that is the service's job.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def execute(self, context: BossMatchContext) -> BossMatchExecution:
        """Run all three model phases and return the validated execution.

        Convenience entry point for callers that do not need per-phase step
        persistence. The service orchestrator calls the individual phase
        methods (``build_prompt`` / ``call_model`` / ``validate``) instead so it
        can record an ``AgentStep`` for each.
        """
        prompt = self.build_prompt(context)
        response = await self.call_model(prompt.messages)
        validated = self.validate(response)
        return BossMatchExecution(
            output=validated,
            truncation=prompt.truncation,
            provider=response.provider,
            model=response.model,
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            usage=_usage_to_dict(response.usage),
        )

    def build_prompt(self, context: BossMatchContext):
        """Build the chat messages + truncation metadata (build_prompt_context)."""
        return build_boss_match_messages(context)

    async def call_model(self, messages) -> ChatResponse:
        """Send the chat request and return the raw response (call_model)."""
        request = ChatRequest(messages=messages, temperature=0.1)
        return await self._gateway.chat(request)

    def validate(self, response: ChatResponse) -> MatchDecisionModelOutput:
        """Parse + schema-validate the model output (validate_model_output).

        Raises :class:`BossMatchValidationError` (kind ``"json"`` or
        ``"schema"``) on failure. The service maps this to the 502
        ``model returned invalid match decision`` response.
        """
        try:
            parsed = json.loads(response.content)
        except (json.JSONDecodeError, TypeError) as exc:
            _log.warning(
                "boss_match.invalid_json",
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise BossMatchValidationError(
                "json",
                "model returned non-JSON content",
                request_id=response.request_id,
            ) from exc

        try:
            return MatchDecisionModelOutput.model_validate(parsed)
        except ValidationError as exc:
            _log.warning(
                "boss_match.schema_validation_failed",
                provider=response.provider,
                model=response.model,
                request_id=response.request_id,
                error=str(exc),
            )
            raise BossMatchValidationError(
                "schema",
                "model output failed schema validation",
                request_id=response.request_id,
            ) from exc


__all__ = [
    "BossMatchExecution",
    "BossMatchExecutor",
    "BossMatchValidationError",
]
