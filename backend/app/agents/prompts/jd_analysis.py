"""Prompt module for the resume-aware JD analysis workflow.

Builds the chat messages passed to ``ModelGateway.chat``. The prompt separates
facts (profile, resume, JD) from instructions, and includes an explicit
no-invention rule for resume facts (per
``.trellis/spec/backend/ai-sdk-integration.md`` Prompt and Output Rules).

Context-size guards cap the resume raw text (~12k chars) and JD text (~8k
chars) included in the prompt; truncation metadata is returned alongside the
messages so the orchestrator can persist it in ``AgentRun.result.source_context``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.prompts.templates.loader import load_prompt_template
from app.models_gateway.base import ChatMessage

PROMPT_VERSION = "jd-analysis-v3"
SYSTEM_PROMPT_TEMPLATE = "jd_analysis_system.md"

# Conservative MVP caps. Resume text tends to be denser than JD text, so the
# resume cap is larger; both are well under typical model context windows.
RESUME_RAW_TEXT_CAP = 12_000
JD_RAW_CAP = 8_000


@dataclass
class JdAnalysisContext:
    """Verified agent context built by the context loader (design.md §7).

    All fields are pre-validated: the job and resume version belong to
    ``user_id``, and ``resume_raw_text`` is non-empty. ``parsed_facts`` is the
    parser telemetry/metadata dict stored on ``ResumeVersion``.
    """

    user_id: str
    profile: dict[str, Any]
    job: dict[str, Any]
    resume: dict[str, Any]


@dataclass
class JdAnalysisPromptResult:
    """Output of :func:`build_jd_analysis_messages`.

    ``truncation`` records how much of each source was dropped so the
    orchestrator can store it as provenance metadata.
    """

    messages: list[ChatMessage]
    truncation: dict[str, Any]


def _truncate(text: str, cap: int) -> tuple[str, int]:
    """Return ``(truncated_text, dropped_chars)`` for ``text`` against ``cap``."""
    if len(text) <= cap:
        return text, 0
    return text[:cap], len(text) - cap


def build_jd_analysis_messages(
    context: JdAnalysisContext,
    *,
    resume_cap: int = RESUME_RAW_TEXT_CAP,
    jd_cap: int = JD_RAW_CAP,
) -> JdAnalysisPromptResult:
    """Build the chat messages for the JD analysis model call.

    The user message is split into clearly labeled sections (profile, resume,
    JD, output schema) so facts stay separate from instructions. Resume and JD
    text are capped; the returned ``truncation`` metadata records how many
    characters were dropped from each.
    """
    profile = context.profile
    job = context.job
    resume = context.resume

    resume_raw = str(resume.get("raw_text") or "")
    jd_raw = str(job.get("jd_raw") or "")
    resume_text, resume_dropped = _truncate(resume_raw, resume_cap)
    jd_text, jd_dropped = _truncate(jd_raw, jd_cap)

    # Typed structured facts extracted from the resume (may be empty when
    # extraction has not run or the format was unsupported). Rendered as a
    # separate block so the model can reason over facts in addition to
    # raw_text, per the v2 prompt instructions.
    resume_facts = resume.get("facts") or {}

    profile_lines = [
        f"user_id: {context.user_id}",
        f"display_name: {profile.get('display_name') or ''}",
        f"career_direction: {profile.get('career_direction') or ''}",
        f"base_location: {profile.get('base_location') or ''}",
        f"preferred_locations: {profile.get('preferred_locations') or []}",
        f"salary_min: {profile.get('salary_min')}",
        f"salary_max: {profile.get('salary_max')}",
        f"strengths: {profile.get('strengths') or []}",
        f"deal_breakers: {profile.get('deal_breakers') or ''}",
        f"preferred_company_types: {profile.get('preferred_company_types') or ''}",
        f"preferred_industries: {profile.get('preferred_industries') or ''}",
        f"work_mode_preference: {profile.get('work_mode_preference') or ''}",
        f"commute_preference: {profile.get('commute_preference') or ''}",
        f"career_goals: {profile.get('career_goals') or ''}",
        f"resume_tailoring_notes: {profile.get('resume_tailoring_notes') or ''}",
        f"availability_notes: {profile.get('availability_notes') or ''}",
    ]

    resume_lines = [
        f"resume_id: {resume.get('resume_id') or ''}",
        f"resume_version_id: {resume.get('resume_version_id') or ''}",
        f"filename: {resume.get('filename') or ''}",
        f"parser_status: {resume.get('parser_status') or ''}",
        f"parsed_facts: {resume.get('parsed_facts') or {}}",
        f"facts: {resume_facts}",
        "raw_text:",
        resume_text,
    ]

    job_lines = [
        f"job_id: {job.get('id') or ''}",
        f"company: {job.get('company') or ''}",
        f"title: {job.get('title') or ''}",
        f"location: {job.get('location') or ''}",
        f"salary_range: {job.get('salary_range') or ''}",
        f"direction: {job.get('direction') or ''}",
        "jd_raw:",
        jd_text,
    ]

    schema_lines = [
        "Output JSON schema (field names are exact):",
        "{",
        '  "role_summary": string,',
        '  "responsibilities": string[],',
        '  "hard_requirements": string[],',
        '  "nice_to_have_requirements": string[],',
        (
            '  "resume_match_evidence": [{"claim": string, '
            '"source": "jd"|"resume"|"profile", "quote": string|null}],'
        ),
        (
            '  "risk_points": [{"title": string, "detail": string, '
            '"severity": "low"|"medium"|"high"}],'
        ),
        '  "salary_note": string,',
        '  "growth_note": string,',
        '  "stability_note": string,',
        '  "match_score": integer(0-100)|null,',
        '  "risk_score": integer(0-100)|null,',
        '  "skill_gaps": string[],',
        '  "interview_preparation": string[],',
        ('  "recommendation": "strong_match"|"possible_match"|"weak_match"|"not_enough_info",'),
        '  "salary_structure": {',
        '    "range_text": string|null (JD原文薪资表述),',
        '    "min_value": number|null (归一化下限k/月),',
        '    "max_value": number|null (归一化上限k/月),',
        '    "period": "monthly"|"yearly"|"hourly"|"daily"|"unknown",',
        '    "composition": string[] (底薪/绩效/提成/补贴等构成),',
        '    "caveats": string[] (薪资虚高或不可信的理由，可为空)',
        '  }|null,',
        '  "red_flags": [{',
        '    "flag_type": "training_loan"|"training_fee"|"outsourcing_onsite"',
        '|"inflated_salary"|"long_term_listing"|"other",',
        '    "title": string,',
        '    "detail": string,',
        '    "severity": "low"|"medium"|"high",',
        '    "evidence_quote": string|null (JD原文短引文，≤300字)',
        '  }],',
        '  "stability_signals": [{',
        '    "polarity": "positive"|"negative"|"unknown",',
        '    "signal": string,',
        '    "evidence_quote": string|null (JD原文短引文，≤300字)',
        '  }]',
        "}",
        "",
        "Job-risk lens rules (岗位风险透视):",
        "- red_flags 只报告 JD 文本中有实际依据的问题；每条尽量附 evidence_quote",
        "  （从 JD 原文截取，禁止改写/编造）。无依据就返回空列表。",
        "- 重点识别：培训贷/岗前培训费或押金话术、外包驻场、薪资区间过宽或",
        "  与经验要求不匹配的虚高、常年挂单迹象（如\"长期招聘\"表述）。",
        "- salary_structure 只在 JD 提及薪资时填写；无法归一化的数值置 null。",
        "- stability_signals 反映公司稳定性线索（如外包性质、团队描述缺失）。",
    ]

    user_content = "\n".join(
        [
            "## USER PROFILE",
            *profile_lines,
            "",
            "## RESUME",
            *resume_lines,
            "",
            "## JOB DESCRIPTION",
            *job_lines,
            "",
            "## REQUIRED OUTPUT",
            *schema_lines,
        ]
    )

    messages = [
        ChatMessage(role="system", content=load_prompt_template(SYSTEM_PROMPT_TEMPLATE)),
        ChatMessage(role="user", content=user_content),
    ]

    truncation = {
        "resume_raw_text_total_chars": len(resume_raw),
        "resume_raw_text_dropped_chars": resume_dropped,
        "jd_raw_total_chars": len(jd_raw),
        "jd_raw_dropped_chars": jd_dropped,
        "resume_cap": resume_cap,
        "jd_cap": jd_cap,
    }

    return JdAnalysisPromptResult(messages=messages, truncation=truncation)
