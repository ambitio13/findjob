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

# Marker present in the resume fact extraction system prompt built by
# ``build_resume_fact_messages``. Routes the fake gateway to the extraction
# branch without inspecting provider-specific metadata.
_RESUME_FACT_MARKER = "resume fact extraction assistant"

# Marker present in the JD paste parsing system prompt built by
# ``build_jd_parse_messages``. Routes the fake gateway to the parse branch
# without inspecting provider-specific metadata.
_JD_PASTE_MARKER = "jd paste parsing assistant"


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

        if _is_resume_fact_prompt(request.messages):
            content = json.dumps(_RESUME_FACT_FAKE_OUTPUT, ensure_ascii=False)
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
                raw={"resume_fact_extraction": True},
            )

        if _is_jd_paste_prompt(request.messages):
            content = json.dumps(_JD_PASTE_FAKE_OUTPUT, ensure_ascii=False)
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
                raw={"jd_paste_parsing": True},
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


def _is_resume_fact_prompt(messages: list) -> bool:
    """True when the message set looks like a resume fact extraction prompt.

    Detection relies on the marker the resume fact system message injects,
    mirroring ``_is_jd_analysis_prompt``.
    """
    return any(_RESUME_FACT_MARKER in m.content for m in messages)


def _is_jd_paste_prompt(messages: list) -> bool:
    """True when the message set looks like a JD paste parsing prompt.

    Detection relies on the marker the JD paste system message injects,
    mirroring ``_is_jd_analysis_prompt``.
    """
    return any(_JD_PASTE_MARKER in m.content for m in messages)


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


# Deterministic, schema-valid resume fact extraction payload. Every field is
# optional, but we populate the common ones so the executor and smoke tests can
# run fully offline and assertions are stable. Keeping it constant makes
# executor assertions stable.
_RESUME_FACT_FAKE_OUTPUT = {
    "contact": {"name": "张三", "email": "zhangsan@example.com", "phone": "13800000000"},
    "education": [
        {"school": "某大学", "degree": "本科", "major": "计算机科学", "period": "2016-2020"},
    ],
    "work_experience": [
        {
            "company": "某公司",
            "title": "后端工程师",
            "period": "2020-至今",
            "summary": "负责后端 API 设计与实现。",
        },
    ],
    "projects": [
        {"name": "简历投递 Agent", "role": "后端", "summary": "构建自动化投递系统。"},
    ],
    "skills": ["Python", "FastAPI", "PostgreSQL"],
    "years_of_experience": 5,
    "target_direction": "后端工程",
    "locations": ["北京"],
    "strengths": ["系统设计", "Python 生态"],
    "highlights": ["主导核心服务重构"],
    "uncertain_fields": [],
}


# Deterministic, schema-valid JD paste parsing payload. Every field is
# optional, but we populate the common ones so the executor and smoke tests can
# run fully offline and assertions are stable. Keeping it constant makes
# executor assertions stable.
_JD_PASTE_FAKE_OUTPUT = {
    "title": "后端工程师",
    "company": "某科技公司",
    "platform": "manual",
    "location": "北京",
    "salary_range": "25k-40k",
    "direction": "后端工程",
    "responsibilities": ["负责后端 API 设计与实现", "维护核心服务稳定性"],
    "hard_requirements": ["Python", "FastAPI", "PostgreSQL"],
    "nice_to_have_requirements": ["Kafka", "Redis"],
    "benefits_or_risk_clues": ["弹性工作", "期权激励"],
    "uncertain_fields": [],
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
