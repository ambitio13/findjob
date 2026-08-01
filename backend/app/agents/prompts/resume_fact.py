"""Prompt module for resume fact extraction.

Builds the chat messages passed to ``ModelGateway.chat``. The prompt separates
facts (raw resume text) from instructions, and includes an explicit
no-fabrication rule: the model must mark uncertain fields rather than invent
them (per ``.trellis/spec/backend/ai-sdk-integration.md`` Prompt and Output
Rules).

A context-size guard caps the resume raw text (~12k chars) included in the
prompt, mirroring ``RESUME_RAW_TEXT_CAP`` in
:mod:`app.agents.prompts.jd_analysis`; truncation metadata is returned so the
orchestrator can persist it in the ``AgentRun`` result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.prompts.templates.loader import load_prompt_template
from app.models_gateway.base import ChatMessage

PROMPT_VERSION = "resume-fact-extraction-v1"
SYSTEM_PROMPT_TEMPLATE = "resume_fact_system.md"

# Conservative MVP cap. Resume text tends to be dense; the cap stays well under
# typical model context windows and mirrors the JD-analysis resume cap.
RESUME_RAW_TEXT_CAP = 12_000


@dataclass
class ResumeFactPromptResult:
    """Output of :func:`build_resume_fact_messages`.

    ``truncation`` records how much of the source was dropped so the
    orchestrator can store it as provenance metadata.
    """

    messages: list[ChatMessage]
    truncation: dict[str, Any]


def _truncate(text: str, cap: int) -> tuple[str, int]:
    """Return ``(truncated_text, dropped_chars)`` for ``text`` against ``cap``."""
    if len(text) <= cap:
        return text, 0
    return text[:cap], len(text) - cap


def build_resume_fact_messages(
    raw_text: str,
    filename: str,
    *,
    resume_cap: int = RESUME_RAW_TEXT_CAP,
) -> ResumeFactPromptResult:
    """Build the chat messages for the resume fact extraction model call.

    The user message is split into clearly labeled sections (source metadata,
    resume text, output schema) so facts stay separate from instructions.
    Resume text is capped; the returned ``truncation`` metadata records how
    many characters were dropped.
    """
    resume_text, resume_dropped = _truncate(raw_text, resume_cap)

    source_lines = [
        f"filename: {filename}",
        f"raw_text_total_chars: {len(raw_text)}",
        f"raw_text_dropped_chars: {resume_dropped}",
    ]

    schema_lines = [
        "Output JSON schema (field names are exact):",
        "{",
        '  "contact": {"name": string|null, "email": string|null, "phone": string|null},',
        (
            '  "education": [{"school": string|null, "degree": string|null, '
            '"major": string|null, "period": string|null}],'
        ),
        (
            '  "work_experience": [{"company": string|null, "title": string|null, '
            '"period": string|null, "summary": string|null}],'
        ),
        ('  "projects": [{"name": string|null, "role": string|null, "summary": string|null}],'),
        '  "skills": string[],',
        '  "years_of_experience": integer|null,',
        '  "target_direction": string|null,',
        '  "locations": string[],',
        '  "strengths": string[],',
        '  "highlights": string[],',
        '  "uncertain_fields": [{"field": string, "reason": string|null}]',
        "}",
    ]

    user_content = "\n".join(
        [
            "## SOURCE",
            *source_lines,
            "",
            "## RESUME TEXT",
            resume_text,
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
        "resume_raw_text_total_chars": len(raw_text),
        "resume_raw_text_dropped_chars": resume_dropped,
        "resume_cap": resume_cap,
    }

    return ResumeFactPromptResult(messages=messages, truncation=truncation)
