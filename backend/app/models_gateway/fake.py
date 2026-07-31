"""Fake model gateway for tests and no-key local development.

Returns deterministic, schema-valid responses without any network access.
This is the default provider when ``MODEL_API_KEY`` is empty.

The JD-analysis branch is detected by the prompt marker that
:func:`app.agents.prompts.jd_analysis.build_jd_analysis_messages` injects into
its system message. When the branch matches, ``chat`` returns JSON that passes
``JdAnalysisModelOutput.model_validate`` so the executor and the smoke tests can
run fully offline. All other prompts get the original echo behavior.
"""

from __future__ import annotations

import json

from app.models_gateway.base import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    ModelGateway,
    StructuredRequest,
)

# Marker present in the JD-analysis system prompt built by
# ``build_jd_analysis_messages``. Used to route the fake gateway without
# inspecting provider-specific metadata.
_JD_ANALYSIS_MARKER = "career-focused JD analysis assistant"


class FakeModelGateway(ModelGateway):
    provider_name = "fake"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        if _is_jd_analysis_prompt(request.messages):
            content = json.dumps(_JD_ANALYSIS_FAKE_OUTPUT, ensure_ascii=False)
            usage = ChatUsage(
                prompt_tokens=len(content) + sum(len(m.content) for m in request.messages),
                completion_tokens=len(content),
                total_tokens=len(content) * 2,
            )
            return ChatResponse(
                content=content,
                model=request.model or "fake-model",
                provider=self.provider_name,
                request_id=request.request_id,
                latency_ms=1,
                usage=usage,
                raw={"jd_analysis": True},
            )

        joined = " | ".join(m.content for m in request.messages)
        content = f"[fake-model] echo: {joined[:200]}"
        usage = ChatUsage(
            prompt_tokens=len(joined),
            completion_tokens=len(content),
            total_tokens=len(joined) + len(content),
        )
        return ChatResponse(
            content=content,
            model=request.model or "fake-model",
            provider=self.provider_name,
            request_id=request.request_id,
            latency_ms=1,
            usage=usage,
            raw={"echo": True},
        )

    async def structured(self, request: StructuredRequest) -> object:
        # Emit a minimal valid instance of the requested model. Fields with
        # defaults stay default; required fields are filled heuristically.
        sample = _sample_instance(request.response_model)
        return sample


def _is_jd_analysis_prompt(messages: list) -> bool:
    """True when the message set looks like a JD-analysis prompt.

    Detection relies on the marker the JD-analysis system message injects. This
    keeps the fake gateway decoupled from prompt internals while still routing
    the branch cleanly.
    """
    return any(_JD_ANALYSIS_MARKER in m.content for m in messages)


# Deterministic, schema-valid JD-analysis payload. Every required field is set
# and scores are within ``[0, 100]`` so ``JdAnalysisModelOutput.model_validate``
# always passes. Keeping it constant makes executor assertions stable.
_JD_ANALYSIS_FAKE_OUTPUT = {
    "role_summary": "Backend engineer role analyzed against the resume.",
    "responsibilities": ["Build APIs", "Own backend services"],
    "hard_requirements": ["Python", "FastAPI"],
    "nice_to_have_requirements": ["Kafka"],
    "resume_match_evidence": [
        {"claim": "Resume shows Python experience", "source": "resume", "quote": "Python"},
        {"claim": "JD requires Python", "source": "jd", "quote": "Python"},
    ],
    "risk_points": [
        {"title": "Kafka gap", "detail": "Resume does not mention Kafka.", "severity": "medium"},
    ],
    "salary_note": "Within market range.",
    "growth_note": "Strong growth trajectory.",
    "stability_note": "Stable company.",
    "match_score": 78,
    "risk_score": 34,
    "skill_gaps": ["Kafka"],
    "interview_preparation": ["Review Kafka basics"],
    "recommendation": "possible_match",
}


def _sample_instance(model_cls: type) -> object:
    """Best-effort construction of a Pydantic model with sensible defaults."""
    try:
        return model_cls.model_validate_json("{}")
    except Exception:
        pass

    # Fall back to constructing from JSON-like defaults derived from field types.
    data: dict[str, object] = {}
    for name, field in model_cls.model_fields.items():
        if field.is_required():
            data[name] = _default_for(field)
    try:
        return model_cls.model_validate(data)
    except Exception:
        # Last resort: return a plain dict so callers can still introspect.
        return data  # type: ignore[return-value]


def _default_for(field) -> object:  # type: ignore[no-untyped-def]
    ann = field.annotation
    if ann is str or ann == "str":
        return ""
    if ann is int or ann == "int":
        return 0
    if ann is float or ann == "float":
        return 0.0
    if ann is bool or ann == "bool":
        return False
    return {}
