"""Prompt module for the readiness artifact generation workflow.

Builds the chat messages passed to ``ModelGateway.chat`` for each of the four
readiness artifact types. The system template is shared; the user message
varies by ``artifact_type`` so the model knows which output schema to produce.

Context-size guards cap the resume raw text (~12k chars) and JD text (~8k
chars) included in the prompt; truncation metadata is returned alongside the
messages so the orchestrator can persist it in ``AgentRun.result.source_context``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.prompts.templates.loader import load_prompt_template
from app.models_gateway.base import ChatMessage
from app.schemas.readiness import ReadinessArtifactType

PROMPT_VERSION = "readiness-v1"
SYSTEM_PROMPT_TEMPLATE = "readiness_system.md"

# Conservative MVP caps. Resume text tends to be denser than JD text, so the
# resume cap is larger; both are well under typical model context windows.
RESUME_RAW_TEXT_CAP = 12_000
JD_RAW_CAP = 8_000

#: Marker injected into the system prompt so the fake gateway can route
#: readiness-generation prompts without inspecting provider metadata.
_READINESS_MARKER = "career readiness assistant"

#: Per-artifact-type output schema descriptions injected into the user message.
_SCHEMA_LINES: dict[str, list[str]] = {
    ReadinessArtifactType.hr_opening_message.value: [
        "Output JSON schema (field names are exact):",
        "{",
        '  "hook": string (20字以内亮点钩子),',
        '  "message": string (完整可复制话术),',
        '  "evidence": string[] (来源支撑要点),',
        '  "risk_note": string|null (可选风险提示)',
        "}",
    ],
    ReadinessArtifactType.resume_rewrite_snippet.value: [
        "Output JSON schema (field names are exact):",
        "{",
        '  "project_snippets": string[],',
        '  "skill_snippets": string[],',
        '  "experience_snippets": string[],',
        '  "do_not_claim": string[]',
        "}",
    ],
    ReadinessArtifactType.skill_gap_plan.value: [
        "Output JSON schema (field names are exact):",
        "{",
        '  "critical_gaps": string[],',
        '  "quick_wins": string[],',
        '  "study_plan": string[],',
        '  "interview_risk": string[]',
        "}",
    ],
    ReadinessArtifactType.interview_prep.value: [
        "Output JSON schema (field names are exact):",
        "{",
        '  "likely_questions": string[],',
        '  "answer_points": string[],',
        '  "portfolio_talking_points": string[],',
        '  "questions_to_ask_interviewer": string[]',
        "}",
    ],
}


@dataclass
class ReadinessContext:
    """Verified agent context built by the readiness service.

    All fields are pre-validated: the job and resume version belong to
    ``user_id``, and ``resume_raw_text`` is non-empty. ``artifact_type``
    selects which output schema the model must produce.
    """

    user_id: str
    artifact_type: str
    profile: dict[str, Any]
    job: dict[str, Any]
    resume: dict[str, Any]
    source_hash: str


@dataclass
class ReadinessPromptResult:
    """Output of :func:`build_readiness_messages`.

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


def build_readiness_messages(
    context: ReadinessContext,
    *,
    resume_cap: int = RESUME_RAW_TEXT_CAP,
    jd_cap: int = JD_RAW_CAP,
) -> ReadinessPromptResult:
    """Build the chat messages for the readiness artifact model call.

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

    schema_lines = _SCHEMA_LINES.get(
        context.artifact_type,
        _SCHEMA_LINES[ReadinessArtifactType.hr_opening_message.value],
    )

    artifact_instruction = (
        f"Generate a '{context.artifact_type}' artifact for this job application. "
        "Tailor it specifically to the candidate's resume and the job description below."
    )

    user_content = "\n".join(
        [
            f"## TASK\n{artifact_instruction}",
            "",
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

    # Inject the marker so the fake gateway can route readiness prompts. The
    # marker is embedded via template substitution so the system prompt stays
    # human-editable without code changes.
    system_content = load_prompt_template(
        SYSTEM_PROMPT_TEMPLATE,
        # The template already contains "career readiness assistant" so no
        # substitution is needed — the marker is part of the static text.
    )

    messages = [
        ChatMessage(role="system", content=system_content),
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

    return ReadinessPromptResult(messages=messages, truncation=truncation)


def readiness_marker() -> str:
    """Return the marker string the fake gateway uses to detect readiness prompts."""
    return _READINESS_MARKER
