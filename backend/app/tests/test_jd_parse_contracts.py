"""Contract tests for the JD paste parsing workflow.

Covers:
- :class:`JdPasteFactsModelOutput` schema validation (valid full, valid sparse,
  invalid missing required ``field`` in ``UncertainField``);
- :func:`build_jd_parse_messages` prompt builder (marker present, schema
  inlined, truncation when JD > cap, platform hint included);
- :class:`JdPasteExecutor.validate` (valid JSON → output, invalid JSON →
  ``kind="json"``, schema mismatch → ``kind="schema"``).

The model gateway is never invoked here except for the executor.validate tests
which synthesize :class:`ChatResponse` objects directly.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.jd_paste_executor import JdPasteExecutor, JdPasteValidationError
from app.agents.prompts.jd_paste import (
    _JD_PASTE_MARKER,
    JD_RAW_CAP,
    PROMPT_VERSION,
    build_jd_parse_messages,
)
from app.models_gateway.base import ChatResponse
from app.models_gateway.fake import _is_jd_paste_prompt
from app.schemas.jd_paste_facts import JdPasteFactsModelOutput, UncertainField

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_output_dict() -> dict[str, Any]:
    return {
        "title": "后端工程师",
        "company": "某科技公司",
        "platform": "manual",
        "location": "北京",
        "salary_range": "25k-40k",
        "direction": "后端工程",
        "responsibilities": ["负责后端 API 设计与实现"],
        "hard_requirements": ["Python", "FastAPI"],
        "nice_to_have_requirements": ["Kafka"],
        "benefits_or_risk_clues": ["弹性工作"],
        "uncertain_fields": [],
    }


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------


def test_model_output_accepts_valid_payload() -> None:
    out = JdPasteFactsModelOutput.model_validate(_valid_output_dict())
    assert out.title == "后端工程师"
    assert out.company == "某科技公司"
    assert out.hard_requirements == ["Python", "FastAPI"]
    assert out.uncertain_fields == []


def test_model_output_accepts_sparse_payload() -> None:
    """All fields are optional, so a sparse JD produces a valid object."""
    out = JdPasteFactsModelOutput.model_validate(
        {"uncertain_fields": [{"field": "salary_range", "reason": "not stated"}]}
    )
    assert out.title is None
    assert out.company is None
    assert out.responsibilities == []
    assert len(out.uncertain_fields) == 1


def test_model_output_accepts_empty_object() -> None:
    out = JdPasteFactsModelOutput.model_validate({})
    assert out.title is None
    assert out.responsibilities == []


def test_model_output_rejects_uncertain_field_missing_field() -> None:
    data = _valid_output_dict()
    data["uncertain_fields"] = [{"reason": "missing field key"}]
    with pytest.raises(ValidationError):
        JdPasteFactsModelOutput.model_validate(data)


def test_uncertain_field_submodel() -> None:
    uf = UncertainField(field="location", reason="ambiguous")
    assert uf.field == "location"
    assert uf.reason == "ambiguous"
    # reason is optional.
    uf2 = UncertainField(field="title")
    assert uf2.reason is None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def test_prompt_version_constant() -> None:
    assert PROMPT_VERSION == "jd-paste-parsing-v1"


def test_jd_raw_cap_constant() -> None:
    assert JD_RAW_CAP == 8_000


def test_build_messages_returns_system_and_user() -> None:
    result = build_jd_parse_messages("Hire a Python backend engineer.")
    assert len(result.messages) == 2
    assert result.messages[0].role == "system"
    assert result.messages[1].role == "user"


def test_build_messages_includes_marker() -> None:
    """The fake-gateway marker must be present in the system prompt."""
    result = build_jd_parse_messages("some JD text")
    system = result.messages[0].content
    assert _JD_PASTE_MARKER in system


def test_build_messages_includes_no_fabrication_rule() -> None:
    result = build_jd_parse_messages("some JD text")
    system = result.messages[0].content
    assert "NEVER fabricate" in system


def test_build_messages_includes_all_source_sections() -> None:
    result = build_jd_parse_messages("Python backend engineer at Acme.")
    user = result.messages[1].content
    assert "## SOURCE" in user
    assert "## JD TEXT" in user
    assert "## REQUIRED OUTPUT" in user
    assert "Python backend engineer at Acme." in user


def test_build_messages_includes_schema_in_user_message() -> None:
    result = build_jd_parse_messages("some JD")
    user = result.messages[1].content
    assert "title" in user
    assert "company" in user
    assert "responsibilities" in user
    assert "uncertain_fields" in user


def test_build_messages_includes_platform_hint() -> None:
    result = build_jd_parse_messages("some JD", platform_hint="lagou")
    user = result.messages[1].content
    assert "lagou" in user


def test_build_messages_platform_hint_defaults_to_unknown() -> None:
    result = build_jd_parse_messages("some JD")
    user = result.messages[1].content
    assert "platform_hint: unknown" in user


def test_build_messages_truncates_when_over_cap() -> None:
    big_jd = "J" * (JD_RAW_CAP + 500)
    result = build_jd_parse_messages(big_jd, jd_cap=JD_RAW_CAP)
    assert result.truncation["raw_jd_total_chars"] == len(big_jd)
    assert result.truncation["raw_jd_dropped_chars"] == 500
    # The user message contains the capped text, not the full text.
    assert ("J" * JD_RAW_CAP) in result.messages[1].content
    assert ("J" * len(big_jd)) not in result.messages[1].content


def test_build_messages_no_truncation_when_under_cap() -> None:
    result = build_jd_parse_messages("short JD")
    assert result.truncation["raw_jd_dropped_chars"] == 0
    assert result.truncation["raw_jd_total_chars"] == len("short JD")


def test_build_messages_custom_cap_overrides_default() -> None:
    result = build_jd_parse_messages("1234567890", jd_cap=4)
    assert result.truncation["raw_jd_dropped_chars"] == 6
    assert "1234" in result.messages[1].content


def test_fake_gateway_detects_jd_paste_prompt() -> None:
    """The fake gateway's marker detection must recognize the JD paste prompt."""
    result = build_jd_parse_messages("some JD text")
    assert _is_jd_paste_prompt(result.messages) is True


# ---------------------------------------------------------------------------
# Executor.validate
# ---------------------------------------------------------------------------


def _make_response(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="fake-model",
        provider="fake",
        request_id="req_test",
        latency_ms=1,
        usage=None,
    )


def test_executor_validate_valid_json() -> None:
    executor = JdPasteExecutor.__new__(JdPasteExecutor)  # bypass __init__ (no gateway needed)
    response = _make_response(json.dumps(_valid_output_dict()))
    output = executor.validate(response)
    assert output.title == "后端工程师"
    assert output.company == "某科技公司"


def test_executor_validate_invalid_json() -> None:
    executor = JdPasteExecutor.__new__(JdPasteExecutor)
    response = _make_response("not json at all")
    with pytest.raises(JdPasteValidationError) as exc_info:
        executor.validate(response)
    assert exc_info.value.kind == "json"
    assert exc_info.value.request_id == "req_test"


def test_executor_validate_schema_mismatch() -> None:
    """JSON that fails schema validation raises kind='schema'."""
    executor = JdPasteExecutor.__new__(JdPasteExecutor)
    # uncertain_fields with a missing required ``field`` triggers schema failure.
    response = _make_response(json.dumps({"uncertain_fields": [{"reason": "no field key"}]}))
    with pytest.raises(JdPasteValidationError) as exc_info:
        executor.validate(response)
    assert exc_info.value.kind == "schema"
    assert exc_info.value.request_id == "req_test"
