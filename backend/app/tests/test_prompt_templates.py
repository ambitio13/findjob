"""Prompt template loading contracts.

These tests keep the editable system-prompt templates wired to the prompt
builders. The builders still own source/context rendering and JSON schema
sections; the template files own the model-facing instruction text.
"""

from __future__ import annotations

from app.agents.prompts.jd_analysis import (
    SYSTEM_PROMPT_TEMPLATE as JD_ANALYSIS_TEMPLATE,
)
from app.agents.prompts.jd_analysis import (
    JdAnalysisContext,
    build_jd_analysis_messages,
)
from app.agents.prompts.jd_paste import (
    _JD_PASTE_MARKER,
    build_jd_parse_messages,
)
from app.agents.prompts.jd_paste import (
    SYSTEM_PROMPT_TEMPLATE as JD_PASTE_TEMPLATE,
)
from app.agents.prompts.resume_fact import (
    SYSTEM_PROMPT_TEMPLATE as RESUME_FACT_TEMPLATE,
)
from app.agents.prompts.resume_fact import (
    build_resume_fact_messages,
)
from app.agents.prompts.templates.loader import load_prompt_template


def test_jd_analysis_system_prompt_is_loaded_from_template() -> None:
    result = build_jd_analysis_messages(
        JdAnalysisContext(
            user_id="user_1",
            profile={"display_name": "张三"},
            job={"id": "job_1", "jd_raw": "Python backend engineer"},
            resume={"resume_id": "resume_1", "resume_version_id": "v1", "raw_text": "Python"},
        )
    )

    system = result.messages[0].content
    assert system == load_prompt_template(JD_ANALYSIS_TEMPLATE)
    assert "career-focused JD analysis assistant" in system
    assert "NEVER invent resume facts" in system


def test_jd_paste_system_prompt_is_loaded_from_template_with_marker() -> None:
    result = build_jd_parse_messages("招聘 Python 后端工程师")

    system = result.messages[0].content
    expected = load_prompt_template(
        JD_PASTE_TEMPLATE,
        variables={"JD_PASTE_MARKER": _JD_PASTE_MARKER},
    )
    assert system == expected
    assert _JD_PASTE_MARKER in system
    assert "NEVER fabricate job facts" in system


def test_resume_fact_system_prompt_is_loaded_from_template() -> None:
    result = build_resume_fact_messages("Python FastAPI PostgreSQL", "resume.txt")

    system = result.messages[0].content
    assert system == load_prompt_template(RESUME_FACT_TEMPLATE)
    assert "resume fact extraction assistant" in system
    assert "NEVER fabricate resume facts" in system
