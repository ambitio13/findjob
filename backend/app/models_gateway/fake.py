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
import re

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

# Marker present in the readiness artifact generation system prompt built by
# ``build_readiness_messages``. Routes the fake gateway to the readiness branch
# without inspecting provider-specific metadata.
_READINESS_MARKER = "career readiness assistant"

# Marker present in the boss match decision system prompt built by
# ``build_boss_match_messages``. Routes the fake gateway to the boss-match
# branch without inspecting provider-specific metadata.
_BOSS_MATCH_MARKER = "boss recommended job match decision assistant"


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

        if _is_readiness_prompt(request.messages):
            artifact_type = _detect_readiness_artifact_type(request.messages)
            if artifact_type == "targeted_resume":
                fake_output = _targeted_resume_fake_output(request.messages)
            else:
                fake_output = _READINESS_FAKE_OUTPUTS.get(
                    artifact_type, _READINESS_FAKE_OUTPUTS["hr_opening_message"]
                )
            content = json.dumps(fake_output, ensure_ascii=False)
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
                raw={"readiness_generation": True, "artifact_type": artifact_type},
            )

        if _is_boss_match_prompt(request.messages):
            scenario = _detect_boss_match_scenario(request.messages)
            fake_output = _BOSS_MATCH_FAKE_OUTPUTS.get(
                scenario, _BOSS_MATCH_FAKE_OUTPUTS["communicate"]
            )
            content = json.dumps(fake_output, ensure_ascii=False)
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
                raw={"boss_match_decision": True, "scenario": scenario},
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


def _is_readiness_prompt(messages: list) -> bool:
    """True when the message set looks like a readiness artifact generation prompt.

    Detection relies on the marker the readiness system message injects,
    mirroring ``_is_jd_analysis_prompt``.
    """
    return any(_READINESS_MARKER in m.content for m in messages)


def _is_boss_match_prompt(messages: list) -> bool:
    """True when the message set looks like a boss match decision prompt.

    Detection relies on the marker the boss match system message injects,
    mirroring ``_is_jd_analysis_prompt``.
    """
    return any(_BOSS_MATCH_MARKER in m.content for m in messages)


def _detect_boss_match_scenario(messages: list) -> str:
    """Return the match-decision scenario requested in a boss match prompt.

    The user message may include a ``## SCENARIO: {scenario}`` hint so tests
    can exercise the skip and needs_review branches. When absent, the default
    ``"communicate"`` scenario is returned so the fake gateway always produces
    a schema-valid output.
    """
    for msg in messages:
        for scenario in ("communicate", "skip", "needs_review"):
            if f"## SCENARIO: {scenario}" in msg.content:
                return scenario
    return "communicate"


def _detect_readiness_artifact_type(messages: list) -> str:
    """Return the artifact type requested in a readiness prompt.

    The user message includes a ``Generate a '{artifact_type}' artifact`` line;
    this function extracts the type so the fake gateway can return the matching
    schema-valid output.
    """
    for msg in messages:
        for at in (
            "targeted_resume",
            "hr_opening_message",
            "resume_rewrite_snippet",
            "skill_gap_plan",
            "interview_prep",
        ):
            if f"'{at}'" in msg.content:
                return at
    return "hr_opening_message"


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
    # Phase 3 job-risk lens: deterministic so E2E tests can assert the
    # red-flag summary persisted on JobAnalysis.red_flags.
    "salary_structure": {
        "range_text": "25k-40k",
        "min_value": 25.0,
        "max_value": 40.0,
        "period": "monthly",
        "composition": ["底薪", "绩效"],
        "caveats": [],
    },
    "red_flags": [
        {
            "flag_type": "inflated_salary",
            "title": "薪资区间过宽",
            "detail": "25k-40k 区间跨度大，实际 offer 可能贴近下限。",
            "severity": "low",
            "evidence_quote": "25k-40k",
        },
    ],
    "stability_signals": [
        {
            "polarity": "unknown",
            "signal": "JD 未描述团队规模与汇报线。",
            "evidence_quote": None,
        },
    ],
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


# Deterministic, schema-valid readiness artifact payloads. One per artifact
# type, each matching its Pydantic output model so the executor and tests can
# run fully offline. Keeping them constant makes assertions stable.
_READINESS_FAKE_OUTPUTS = {
    "hr_opening_message": {
        "hook": "5年Python后端经验",
        "message": (
            "您好，我是一名有5年Python后端开发经验的工程师，"
            "对贵司的后端工程师职位非常感兴趣。"
        ),
        "evidence": ["简历中提到Python 5年经验", "JD要求Python后端"],
        "risk_note": "Kafka经验不足",
    },
    "resume_rewrite_snippet": {
        "project_snippets": ["主导核心服务重构，QPS提升3倍"],
        "skill_snippets": ["Python, FastAPI, PostgreSQL"],
        "experience_snippets": ["某公司后端工程师，负责API设计与实现"],
        "do_not_claim": ["Kafka深度经验", "前端开发能力"],
    },
    "skill_gap_plan": {
        "critical_gaps": ["Kafka消息队列"],
        "quick_wins": ["复习Redis基础"],
        "study_plan": ["第1周：Kafka基础概念", "第2周：Kafka实战项目"],
        "interview_risk": ["Kafka相关面试题可能无法回答"],
    },
    "interview_prep": {
        "likely_questions": ["请介绍一下你的Python后端经验", "如何设计高并发API"],
        "answer_points": ["重点描述FastAPI项目经验", "结合QPS提升3倍的案例"],
        "portfolio_talking_points": ["核心服务重构项目", "API性能优化"],
        "questions_to_ask_interviewer": ["团队的CI/CD流程是怎样的", "技术栈未来规划"],
    },
}


#: Matches the numbered resume-fact lines the targeted_resume prompt injects
#: (``- [E1] education: ...``). Only these list lines are harvested — schema
#: examples elsewhere in the prompt are deliberately ignored.
_TARGETED_RESUME_FACT_REF_RE = re.compile(r"^- \[([A-Z]+\d+)\] ", re.MULTILINE)


def _targeted_resume_fake_output(messages: list) -> dict:
    """Build a deterministic ``targeted_resume`` payload.

    Fact refs are harvested from the ``## NUMBERED RESUME FACTS`` lines the
    prompt builder injected, so the output passes the executor's
    traceability gate whenever structured facts exist. When no numbered
    facts are present the bullet deliberately cites a nonexistent ID so the
    rejection path runs exactly as it would for a hallucinating provider.
    """
    fact_ids: list[str] = []
    for msg in messages:
        fact_ids.extend(_TARGETED_RESUME_FACT_REF_RE.findall(msg.content))

    bullets = [
        {
            "section": "项目经历",
            "bullet": "主导后端核心服务开发，按 JD 关键词重排突出匹配点（fake）",
            "matched_requirement": "后端 API 设计与实现",
            "source_fact_refs": [fact_ids[0]] if fact_ids else ["E1"],
        },
    ]
    if len(fact_ids) > 1:
        bullets.append(
            {
                "section": "技能",
                "bullet": "具备岗位要求的后端技术栈（fake）",
                "matched_requirement": "后端技术栈要求",
                "source_fact_refs": [fact_ids[1]],
            },
        )

    bullet_lines = "\n".join(
        f"- {b['bullet']}（来源：{'/'.join(b['source_fact_refs'])}）" for b in bullets
    )
    return {
        "headline": "后端工程师 · 针对该岗位的定制简历（fake）",
        "targeted_bullets": bullets,
        "matched_requirements": ["后端 API 设计与实现"],
        "do_not_claim": ["Kafka 深度经验"],
        "one_page_markdown": (
            "# 定制简历（fake）\n\n后端工程师 · 针对该岗位的定制简历\n\n"
            f"## 核心经历\n{bullet_lines}\n"
        ),
    }


# Deterministic, schema-valid boss match decision payloads. One per scenario,
# each matching ``MatchDecisionModelOutput`` so the executor and tests can run
# fully offline. Tests can also patch ``_BOSS_MATCH_FAKE_OUTPUTS`` directly to
# inject edge-case outputs (e.g. invalid JSON, low-score communicate).
_BOSS_MATCH_FAKE_OUTPUTS = {
    "communicate": {
        "decision": "communicate",
        "score": 0.82,
        "reasons": [
            "Resume shows strong Python backend experience matching JD requirements",
            "Salary range and location align with user preferences",
        ],
        "risks": ["Kafka experience is not mentioned in the resume"],
        "missing_requirements": [],
        "opening_message": (
            "您好，我是一名有5年Python后端开发经验的工程师，"
            "对贵司的后端工程师职位非常感兴趣，希望能进一步沟通。"
        ),
    },
    "skip": {
        "decision": "skip",
        "score": 0.25,
        "reasons": [
            "JD requires 8+ years of experience but resume shows 5 years",
            "Location does not match user's preferred cities",
        ],
        "risks": ["Experience gap too large to bridge in short term"],
        "missing_requirements": ["8+ years experience", "Kubernetes production experience"],
        "opening_message": None,
    },
    "needs_review": {
        "decision": "needs_review",
        "score": 0.55,
        "reasons": [
            "Core skills match but salary range is below user expectation",
            "Company stage is uncertain",
        ],
        "risks": ["Salary may not meet minimum expectation", "Company stability unclear"],
        "missing_requirements": ["Salary clarification needed"],
        "opening_message": None,
    },
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
