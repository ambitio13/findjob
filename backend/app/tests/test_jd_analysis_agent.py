"""Phase 3 executor tests for the resume-aware JD analysis workflow.

Covers:
- happy path: ``JdAnalysisExecutor`` + ``FakeModelGateway`` returns a validated
  ``JdAnalysisModelOutput`` with expected fields and provider/model/request
  metadata captured;
- invalid-output path: a stub gateway returning malformed JSON raises
  :class:`JdAnalysisValidationError` with kind ``"json"``;
- schema-invalid path: a stub gateway returning JSON missing required fields /
  out-of-range scores raises with kind ``"schema"``;
- the fake gateway returns schema-valid JSON for the JD-analysis prompt
  (locks the fake branch).

The executor is exercised directly with in-memory contexts and stub gateways;
no DB or network is involved.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.agents.jd_analysis_executor import (
    JdAnalysisExecution,
    JdAnalysisExecutor,
    JdAnalysisValidationError,
)
from app.agents.prompts.jd_analysis import JdAnalysisContext, build_jd_analysis_messages
from app.models_gateway.base import ChatMessage, ChatRequest, ChatResponse, ChatUsage, ModelGateway
from app.models_gateway.fake import FakeModelGateway
from app.schemas.jd_analysis import JdAnalysisModelOutput

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    resume_text: str = "张三\nPython 5年 FastAPI",
    jd_text: str = "Senior Python backend engineer. Build APIs with FastAPI.",
) -> JdAnalysisContext:
    return JdAnalysisContext(
        user_id="exec_user",
        profile={
            "display_name": "测试用户",
            "career_direction": "engineering",
            "base_location": "北京",
            "preferred_locations": ["北京"],
            "salary_min": 20000,
            "salary_max": 40000,
            "strengths": ["Python"],
            "deal_breakers": "不接受996",
            "preferred_company_types": "外企",
            "preferred_industries": "互联网",
            "work_mode_preference": "远程优先",
            "commute_preference": "通勤1小时内",
            "career_goals": "技术专家",
            "resume_tailoring_notes": "突出后端经验",
            "availability_notes": "随时到岗",
        },
        job={
            "id": "job_exec",
            "company": "Acme",
            "title": "Backend Engineer",
            "location": "北京",
            "salary_range": "20-40k",
            "direction": "engineering",
            "jd_raw": jd_text,
        },
        resume={
            "resume_id": "res_exec",
            "resume_version_id": "ver_exec",
            "filename": "r.txt",
            "parser_status": "parsed",
            "raw_text": resume_text,
            "parsed_facts": {"_parser": "text", "_parser_status": "parsed"},
        },
    )


class _StubGateway(ModelGateway):
    """Minimal gateway returning a fixed content string.

    Used to drive the executor's parse/validate failure paths without touching
    the fake JD-analysis branch.
    """

    provider_name = "stub"

    def __init__(self, content: str) -> None:
        self._content = content

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content=self._content,
            model="stub-model",
            provider=self.provider_name,
            request_id=request.request_id,
            latency_ms=0,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


_VALID_OUTPUT: dict[str, Any] = {
    "role_summary": "Backend role analyzed against the resume.",
    "responsibilities": ["Build APIs"],
    "hard_requirements": ["Python", "FastAPI"],
    "nice_to_have_requirements": ["Kafka"],
    "resume_match_evidence": [
        {"claim": "Knows Python", "source": "resume", "quote": "Python 5年"},
    ],
    "risk_points": [
        {"title": "No Kafka", "detail": "Resume lacks Kafka", "severity": "medium"},
    ],
    "salary_note": "Market range.",
    "growth_note": "Good trajectory.",
    "stability_note": "Stable firm.",
    "match_score": 78,
    "risk_score": 34,
    "skill_gaps": ["Kafka"],
    "interview_preparation": ["Review Kafka basics"],
    "recommendation": "possible_match",
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_happy_path_returns_validated_output() -> None:
    executor = JdAnalysisExecutor(gateway=FakeModelGateway())
    result = await executor.execute(_make_context())

    assert isinstance(result, JdAnalysisExecution)
    assert isinstance(result.output, JdAnalysisModelOutput)
    assert result.output.role_summary == "Backend engineer role analyzed against the resume."
    assert result.output.hard_requirements == ["Python", "FastAPI"]
    assert result.output.match_score == 78
    assert result.output.risk_score == 34
    assert result.output.recommendation == "possible_match"
    assert result.output.risk_points[0].severity == "medium"
    assert result.output.resume_match_evidence[0].source == "resume"


@pytest.mark.asyncio
async def test_executor_captures_provider_model_request_metadata() -> None:
    executor = JdAnalysisExecutor(gateway=FakeModelGateway())
    result = await executor.execute(_make_context())

    assert result.provider == "fake"
    assert result.model == "fake-model"
    assert result.request_id.startswith("req_")
    assert result.latency_ms == 1
    assert result.usage is not None
    assert result.usage["prompt_tokens"] is not None
    assert result.usage["completion_tokens"] is not None
    assert result.usage["total_tokens"] is not None


@pytest.mark.asyncio
async def test_executor_carries_truncation_metadata() -> None:
    """The executor must surface prompt truncation metadata for Phase 4 provenance."""
    executor = JdAnalysisExecutor(gateway=FakeModelGateway())
    result = await executor.execute(_make_context())

    expected = build_jd_analysis_messages(_make_context()).truncation
    assert result.truncation == expected
    assert result.truncation["resume_raw_text_dropped_chars"] == 0
    assert result.truncation["jd_raw_dropped_chars"] == 0


@pytest.mark.asyncio
async def test_executor_output_is_persistence_gate() -> None:
    """The returned output must already be a validated JdAnalysisModelOutput.

    Phase 4 persists this object directly; re-validation must not be required.
    """
    executor = JdAnalysisExecutor(gateway=FakeModelGateway())
    result = await executor.execute(_make_context())

    # Round-trip through model_dump to confirm the object is fully constructed.
    dumped = result.output.model_dump()
    re_validated = JdAnalysisModelOutput.model_validate(dumped)
    assert re_validated.match_score == result.output.match_score


# ---------------------------------------------------------------------------
# Invalid-output path: malformed JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_raises_on_malformed_json() -> None:
    gateway = _StubGateway(content="not json at all {")
    executor = JdAnalysisExecutor(gateway=gateway)

    with pytest.raises(JdAnalysisValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "json"
    assert exc_info.value.request_id.startswith("req_")


@pytest.mark.asyncio
async def test_executor_raises_on_empty_content() -> None:
    gateway = _StubGateway(content="")
    executor = JdAnalysisExecutor(gateway=gateway)

    with pytest.raises(JdAnalysisValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "json"


# ---------------------------------------------------------------------------
# Schema-invalid path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_raises_on_missing_required_field() -> None:
    data = copy.deepcopy(_VALID_OUTPUT)
    del data["role_summary"]
    gateway = _StubGateway(content=json.dumps(data))
    executor = JdAnalysisExecutor(gateway=gateway)

    with pytest.raises(JdAnalysisValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "schema"


@pytest.mark.asyncio
async def test_executor_raises_on_out_of_range_score() -> None:
    data = copy.deepcopy(_VALID_OUTPUT)
    data["match_score"] = 101  # > 100
    gateway = _StubGateway(content=json.dumps(data))
    executor = JdAnalysisExecutor(gateway=gateway)

    with pytest.raises(JdAnalysisValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "schema"


@pytest.mark.asyncio
async def test_executor_raises_on_bad_recommendation() -> None:
    data = copy.deepcopy(_VALID_OUTPUT)
    data["recommendation"] = "definitely_match"
    gateway = _StubGateway(content=json.dumps(data))
    executor = JdAnalysisExecutor(gateway=gateway)

    with pytest.raises(JdAnalysisValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "schema"


@pytest.mark.asyncio
async def test_executor_raises_on_bad_severity() -> None:
    data = copy.deepcopy(_VALID_OUTPUT)
    data["risk_points"][0]["severity"] = "critical"
    gateway = _StubGateway(content=json.dumps(data))
    executor = JdAnalysisExecutor(gateway=gateway)

    with pytest.raises(JdAnalysisValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "schema"


@pytest.mark.asyncio
async def test_executor_validation_error_carries_request_id() -> None:
    """The error must carry the gateway request_id so Phase 4 can persist it."""
    data = copy.deepcopy(_VALID_OUTPUT)
    data["risk_score"] = -1
    gateway = _StubGateway(content=json.dumps(data))
    executor = JdAnalysisExecutor(gateway=gateway)

    with pytest.raises(JdAnalysisValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "schema"
    assert exc_info.value.request_id is not None
    assert exc_info.value.request_id.startswith("req_")


# ---------------------------------------------------------------------------
# Fake gateway JD-analysis branch lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_gateway_returns_schema_valid_jd_analysis_json() -> None:
    """The fake gateway must return JSON that passes JdAnalysisModelOutput.

    This is the offline contract that lets the executor and the Phase 4 smoke
    tests run without an API key.
    """
    prompt = build_jd_analysis_messages(_make_context())
    request = ChatRequest(messages=prompt.messages)
    response = await FakeModelGateway().chat(request)

    assert response.provider == "fake"
    # Parse + validate — must not raise.
    output = JdAnalysisModelOutput.model_validate_json(response.content)
    assert output.match_score == 78
    assert output.recommendation == "possible_match"
    assert output.risk_points[0].severity in {"low", "medium", "high"}


@pytest.mark.asyncio
async def test_fake_gateway_preserves_echo_for_non_jd_prompts() -> None:
    """Non-JD prompts still get the original echo behavior (no regression)."""
    request = ChatRequest(messages=[ChatMessage(role="user", content="hello")])
    response = await FakeModelGateway().chat(request)

    assert response.provider == "fake"
    assert "hello" in response.content
    assert response.content.startswith("[fake-model] echo:")
