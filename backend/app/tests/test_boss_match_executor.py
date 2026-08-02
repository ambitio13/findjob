"""Executor + guard tests for the BOSS match-decision workflow.

Covers:

- ``BossMatchExecutor`` + ``FakeModelGateway`` returns a validated
  ``MatchDecisionModelOutput`` with expected fields and provider/model/request
  metadata captured.
- Invalid-output path: a stub gateway returning malformed JSON raises
  ``BossMatchValidationError`` with kind ``"json"``.
- Schema-invalid path: a stub gateway returning JSON with bad decision/score
  raises with kind ``"schema"``.
- The fake gateway returns schema-valid JSON for the boss-match prompt (locks
  the fake branch) for all three scenarios (communicate/skip/needs_review).
- ``validate_opening_message`` length/PII/tone rules.
- ``apply_match_safety_gate`` downgrade rules.

The executor is exercised directly with in-memory contexts and stub gateways;
no DB or network is involved.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.agents.boss_match_executor import (
    BossMatchExecution,
    BossMatchExecutor,
    BossMatchValidationError,
)
from app.agents.opening_message_guard import (
    apply_match_safety_gate,
    validate_opening_message,
)
from app.agents.prompts.boss_match import BossMatchContext, build_boss_match_messages
from app.models_gateway.base import ChatMessage, ChatRequest, ChatResponse, ChatUsage, ModelGateway
from app.models_gateway.fake import _BOSS_MATCH_FAKE_OUTPUTS, FakeModelGateway
from app.schemas.boss_match_decision import MatchDecision, MatchDecisionModelOutput

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    resume_text: str = "张三\nPython 5年 FastAPI",
    jd_text: str = "Senior Python backend engineer. Build APIs with FastAPI.",
) -> BossMatchContext:
    return BossMatchContext(
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
    """Minimal gateway returning a fixed content string."""

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


class _ScenarioGateway(ModelGateway):
    """Gateway that injects a ``## SCENARIO:`` hint to select the fake branch.

    Wraps ``FakeModelGateway`` but appends a scenario hint to the user message
    so ``_detect_boss_match_scenario`` routes to the desired output.
    """

    provider_name = "fake"

    def __init__(self, scenario: str) -> None:
        self._scenario = scenario
        self._inner = FakeModelGateway()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        patched_messages = [
            (
                ChatMessage(role=m.role, content=m.content + f"\n## SCENARIO: {self._scenario}")
                if m.role == "user"
                else m
            )
            for m in request.messages
        ]
        patched_request = ChatRequest(
            messages=patched_messages,
            model=request.model,
            temperature=request.temperature,
        )
        return await self._inner.chat(patched_request)


# ---------------------------------------------------------------------------
# Executor happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_happy_path_returns_validated_output() -> None:
    executor = BossMatchExecutor(gateway=FakeModelGateway())
    result = await executor.execute(_make_context())

    assert isinstance(result, BossMatchExecution)
    assert isinstance(result.output, MatchDecisionModelOutput)
    assert result.output.decision == MatchDecision.communicate
    assert 0.0 <= result.output.score <= 1.0
    assert result.output.opening_message is not None
    assert len(result.output.opening_message) >= 10


@pytest.mark.asyncio
async def test_executor_captures_provider_model_request_metadata() -> None:
    executor = BossMatchExecutor(gateway=FakeModelGateway())
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
    executor = BossMatchExecutor(gateway=FakeModelGateway())
    result = await executor.execute(_make_context())

    expected = build_boss_match_messages(_make_context()).truncation
    assert result.truncation == expected
    assert result.truncation["resume_raw_text_dropped_chars"] == 0
    assert result.truncation["jd_raw_dropped_chars"] == 0


@pytest.mark.asyncio
async def test_executor_output_is_persistence_gate() -> None:
    """The returned output must already be a validated MatchDecisionModelOutput."""
    executor = BossMatchExecutor(gateway=FakeModelGateway())
    result = await executor.execute(_make_context())

    dumped = result.output.model_dump()
    re_validated = MatchDecisionModelOutput.model_validate(dumped)
    assert re_validated.decision == result.output.decision
    assert re_validated.score == result.output.score


# ---------------------------------------------------------------------------
# Invalid-output path: malformed JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_raises_on_malformed_json() -> None:
    gateway = _StubGateway(content="not json at all {")
    executor = BossMatchExecutor(gateway=gateway)

    with pytest.raises(BossMatchValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "json"
    assert exc_info.value.request_id.startswith("req_")


@pytest.mark.asyncio
async def test_executor_raises_on_empty_content() -> None:
    gateway = _StubGateway(content="")
    executor = BossMatchExecutor(gateway=gateway)

    with pytest.raises(BossMatchValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "json"


# ---------------------------------------------------------------------------
# Schema-invalid path
# ---------------------------------------------------------------------------


_VALID_OUTPUT: dict[str, Any] = {
    "decision": "communicate",
    "score": 0.82,
    "reasons": ["Strong match"],
    "risks": ["Kafka gap"],
    "missing_requirements": [],
    "opening_message": "您好，我对该职位非常感兴趣，希望能进一步沟通。",
}


@pytest.mark.asyncio
async def test_executor_raises_on_bad_decision() -> None:
    data = copy.deepcopy(_VALID_OUTPUT)
    data["decision"] = "definitely_match"
    gateway = _StubGateway(content=json.dumps(data))
    executor = BossMatchExecutor(gateway=gateway)

    with pytest.raises(BossMatchValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "schema"


@pytest.mark.asyncio
async def test_executor_raises_on_score_out_of_range() -> None:
    data = copy.deepcopy(_VALID_OUTPUT)
    data["score"] = 1.5  # > 1.0
    gateway = _StubGateway(content=json.dumps(data))
    executor = BossMatchExecutor(gateway=gateway)

    with pytest.raises(BossMatchValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "schema"


@pytest.mark.asyncio
async def test_executor_raises_on_negative_score() -> None:
    data = copy.deepcopy(_VALID_OUTPUT)
    data["score"] = -0.1  # < 0.0
    gateway = _StubGateway(content=json.dumps(data))
    executor = BossMatchExecutor(gateway=gateway)

    with pytest.raises(BossMatchValidationError) as exc_info:
        await executor.execute(_make_context())

    assert exc_info.value.kind == "schema"


# ---------------------------------------------------------------------------
# Fake gateway boss-match branch lock — all three scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_gateway_returns_valid_communicate() -> None:
    prompt = build_boss_match_messages(_make_context())
    request = ChatRequest(messages=prompt.messages)
    response = await FakeModelGateway().chat(request)

    assert response.provider == "fake"
    output = MatchDecisionModelOutput.model_validate_json(response.content)
    assert output.decision == MatchDecision.communicate
    assert output.score >= 0.6
    assert output.opening_message is not None


@pytest.mark.asyncio
async def test_fake_gateway_skip_scenario() -> None:
    gateway = _ScenarioGateway(scenario="skip")
    prompt = build_boss_match_messages(_make_context())
    request = ChatRequest(messages=prompt.messages)
    response = await gateway.chat(request)

    output = MatchDecisionModelOutput.model_validate_json(response.content)
    assert output.decision == MatchDecision.skip
    assert output.opening_message is None


@pytest.mark.asyncio
async def test_fake_gateway_needs_review_scenario() -> None:
    gateway = _ScenarioGateway(scenario="needs_review")
    prompt = build_boss_match_messages(_make_context())
    request = ChatRequest(messages=prompt.messages)
    response = await gateway.chat(request)

    output = MatchDecisionModelOutput.model_validate_json(response.content)
    assert output.decision == MatchDecision.needs_review
    assert output.opening_message is None


# ---------------------------------------------------------------------------
# validate_opening_message
# ---------------------------------------------------------------------------


def test_validate_opening_message_none_is_valid() -> None:
    msg, error = validate_opening_message(None)
    assert msg is None
    assert error is None


def test_validate_opening_message_valid_message() -> None:
    msg, error = validate_opening_message("您好，我对该职位非常感兴趣，希望能进一步沟通。")
    assert msg is not None
    assert error is None


def test_validate_opening_message_empty_string_is_invalid() -> None:
    msg, error = validate_opening_message("")
    assert msg is None
    assert error is not None


def test_validate_opening_message_too_short() -> None:
    msg, error = validate_opening_message("您好")
    assert msg is None
    assert "too short" in error


def test_validate_opening_message_too_long() -> None:
    msg, error = validate_opening_message("啊" * 501)
    assert msg is None
    assert "too long" in error


def test_validate_opening_message_contains_phone_number() -> None:
    msg, error = validate_opening_message("您好，我的手机号是13812345678，请联系我。")
    assert msg is None
    assert "phone" in error


def test_validate_opening_message_contains_email() -> None:
    msg, error = validate_opening_message("您好，请发邮件到test@example.com联系我。")
    assert msg is None
    assert "email" in error


def test_validate_opening_message_contains_id_card() -> None:
    # Construct an 18-digit ID-card-like pattern where no 11-digit subsequence
    # starts with ``1[3-9]`` (so the phone regex does not fire first). Real
    # ID cards always contain a phone-like subsequence (birth years), so we use
    # an artificial alternating pattern to isolate the ID-card branch.
    msg, error = validate_opening_message("您好，证件号101010101010101010请查收。")
    assert msg is None
    assert "ID-card" in error


def test_validate_opening_message_excessive_punctuation() -> None:
    msg, error = validate_opening_message("您好，我对该职位非常感兴趣！！！？？？")
    assert msg is None
    assert "punctuation" in error


# ---------------------------------------------------------------------------
# apply_match_safety_gate
# ---------------------------------------------------------------------------


def test_safety_gate_communicate_high_score_unchanged() -> None:
    output = MatchDecisionModelOutput(
        decision=MatchDecision.communicate,
        score=0.82,
        reasons=["Strong match"],
        risks=[],
        missing_requirements=[],
        opening_message="您好，我对该职位非常感兴趣，希望能进一步沟通。",
    )
    gated = apply_match_safety_gate(output)
    assert gated.decision == MatchDecision.communicate
    assert gated.score == 0.82
    assert gated.opening_message is not None


def test_safety_gate_communicate_low_score_downgraded() -> None:
    output = MatchDecisionModelOutput(
        decision=MatchDecision.communicate,
        score=0.45,
        reasons=["Weak match"],
        risks=[],
        missing_requirements=[],
        opening_message="您好，我对该职位非常感兴趣，希望能进一步沟通。",
    )
    gated = apply_match_safety_gate(output)
    assert gated.decision == MatchDecision.needs_review
    assert gated.opening_message is None


def test_safety_gate_communicate_missing_requirements_downgraded() -> None:
    output = MatchDecisionModelOutput(
        decision=MatchDecision.communicate,
        score=0.82,
        reasons=["Strong match"],
        risks=[],
        missing_requirements=["Kafka experience"],
        opening_message="您好，我对该职位非常感兴趣，希望能进一步沟通。",
    )
    gated = apply_match_safety_gate(output)
    assert gated.decision == MatchDecision.needs_review
    assert gated.opening_message is None


def test_safety_gate_communicate_bad_opening_message_downgraded() -> None:
    output = MatchDecisionModelOutput(
        decision=MatchDecision.communicate,
        score=0.82,
        reasons=["Strong match"],
        risks=[],
        missing_requirements=[],
        opening_message="短",  # too short
    )
    gated = apply_match_safety_gate(output)
    assert gated.decision == MatchDecision.needs_review
    assert gated.opening_message is None


def test_safety_gate_communicate_pii_opening_message_downgraded() -> None:
    output = MatchDecisionModelOutput(
        decision=MatchDecision.communicate,
        score=0.82,
        reasons=["Strong match"],
        risks=[],
        missing_requirements=[],
        opening_message="您好，我的手机号是13812345678，请联系我。",
    )
    gated = apply_match_safety_gate(output)
    assert gated.decision == MatchDecision.needs_review
    assert gated.opening_message is None


def test_safety_gate_skip_unchanged() -> None:
    output = MatchDecisionModelOutput(
        decision=MatchDecision.skip,
        score=0.25,
        reasons=["Mismatch"],
        risks=[],
        missing_requirements=["Experience"],
        opening_message=None,
    )
    gated = apply_match_safety_gate(output)
    assert gated.decision == MatchDecision.skip
    assert gated.score == 0.25


def test_safety_gate_needs_review_unchanged() -> None:
    output = MatchDecisionModelOutput(
        decision=MatchDecision.needs_review,
        score=0.55,
        reasons=["Borderline"],
        risks=[],
        missing_requirements=["Salary clarification"],
        opening_message=None,
    )
    gated = apply_match_safety_gate(output)
    assert gated.decision == MatchDecision.needs_review
    assert gated.score == 0.55


def test_safety_gate_does_not_mutate_input() -> None:
    original = MatchDecisionModelOutput(
        decision=MatchDecision.communicate,
        score=0.45,
        reasons=["Weak match"],
        risks=[],
        missing_requirements=[],
        opening_message="您好，我对该职位非常感兴趣，希望能进一步沟通。",
    )
    gated = apply_match_safety_gate(original)
    assert original.decision == MatchDecision.communicate
    assert original.opening_message is not None
    assert gated.decision == MatchDecision.needs_review
    assert gated.opening_message is None


def test_safety_gate_communicate_none_opening_message_passes() -> None:
    """communicate with None opening_message passes the gate (model didn't provide one).

    The opening-message gate only rejects when the model returned a non-None
    message that fails validation. A None message with a high score is allowed
    (the frontend will prompt the user to write one).
    """
    output = MatchDecisionModelOutput(
        decision=MatchDecision.communicate,
        score=0.82,
        reasons=["Strong match"],
        risks=[],
        missing_requirements=[],
        opening_message=None,
    )
    gated = apply_match_safety_gate(output)
    assert gated.decision == MatchDecision.communicate
    assert gated.opening_message is None


# ---------------------------------------------------------------------------
# Fake outputs directly (ensure schema validity of all three)
# ---------------------------------------------------------------------------


def test_fake_output_communicate_is_schema_valid() -> None:
    output = MatchDecisionModelOutput.model_validate(_BOSS_MATCH_FAKE_OUTPUTS["communicate"])
    assert output.decision == MatchDecision.communicate
    assert output.score >= 0.6
    assert output.opening_message is not None


def test_fake_output_skip_is_schema_valid() -> None:
    output = MatchDecisionModelOutput.model_validate(_BOSS_MATCH_FAKE_OUTPUTS["skip"])
    assert output.decision == MatchDecision.skip
    assert output.opening_message is None


def test_fake_output_needs_review_is_schema_valid() -> None:
    output = MatchDecisionModelOutput.model_validate(_BOSS_MATCH_FAKE_OUTPUTS["needs_review"])
    assert output.decision == MatchDecision.needs_review
    assert output.opening_message is None
