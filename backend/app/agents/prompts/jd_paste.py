"""Prompt module for JD paste parsing.

Builds the chat messages passed to ``ModelGateway.chat``. The prompt separates
facts (raw JD text) from instructions, and includes an explicit no-fabrication
rule: the model must mark uncertain fields rather than invent them (per
``.trellis/spec/backend/ai-sdk-integration.md`` Prompt and Output Rules).

A context-size guard caps the raw JD text (~8k chars) included in the prompt,
mirroring ``RESUME_RAW_TEXT_CAP`` in :mod:`app.agents.prompts.jd_analysis`;
truncation metadata is returned so the orchestrator can persist it in the
``AgentRun`` result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.prompts.templates.loader import load_prompt_template
from app.models_gateway.base import ChatMessage

PROMPT_VERSION = "jd-paste-parsing-v1"
SYSTEM_PROMPT_TEMPLATE = "jd_paste_system.md"

# Conservative MVP cap. JD text varies widely; the cap stays well under typical
# model context windows and mirrors the JD-analysis resume cap.
JD_RAW_CAP = 8_000

#: Marker injected into the system prompt so the fake gateway can route the JD
#: paste branch without inspecting provider-specific metadata (mirrors
#: ``_RESUME_FACT_MARKER`` in :mod:`app.models_gateway.fake`).
_JD_PASTE_MARKER = "jd paste parsing assistant"


@dataclass
class JdPastePromptResult:
    """Output of :func:`build_jd_parse_messages`.

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


def build_jd_parse_messages(
    raw_jd: str,
    platform_hint: str | None = None,
    *,
    jd_cap: int = JD_RAW_CAP,
) -> JdPastePromptResult:
    """Build the chat messages for the JD paste parsing model call.

    The user message is split into clearly labeled sections (source metadata,
    JD text, output schema) so facts stay separate from instructions. JD text is
    capped; the returned ``truncation`` metadata records how many characters
    were dropped.
    """
    jd_text, jd_dropped = _truncate(raw_jd, jd_cap)

    source_lines = [
        f"platform_hint: {platform_hint or 'unknown'}",
        f"raw_jd_total_chars: {len(raw_jd)}",
        f"raw_jd_dropped_chars: {jd_dropped}",
    ]

    schema_lines = [
        "Output JSON schema (field names are exact):",
        "{",
        '  "title": string|null,',
        '  "company": string|null,',
        '  "platform": string|null,',
        '  "location": string|null,',
        '  "salary_range": string|null,',
        '  "direction": string|null,',
        '  "responsibilities": string[],',
        '  "hard_requirements": string[],',
        '  "nice_to_have_requirements": string[],',
        '  "benefits_or_risk_clues": string[],',
        '  "uncertain_fields": [{"field": string, "reason": string|null}]',
        "}",
    ]

    user_content = "\n".join(
        [
            "## SOURCE",
            *source_lines,
            "",
            "## JD TEXT",
            jd_text,
            "",
            "## REQUIRED OUTPUT",
            *schema_lines,
        ]
    )

    messages = [
        ChatMessage(
            role="system",
            content=load_prompt_template(
                SYSTEM_PROMPT_TEMPLATE,
                variables={"JD_PASTE_MARKER": _JD_PASTE_MARKER},
            ),
        ),
        ChatMessage(role="user", content=user_content),
    ]

    truncation = {
        "raw_jd_total_chars": len(raw_jd),
        "raw_jd_dropped_chars": jd_dropped,
        "jd_cap": jd_cap,
    }

    return JdPastePromptResult(messages=messages, truncation=truncation)
