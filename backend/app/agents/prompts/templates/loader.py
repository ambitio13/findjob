"""Load editable prompt templates bundled with the prompt package."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources

_TEMPLATE_PACKAGE = "app.agents.prompts.templates"


def load_prompt_template(filename: str, variables: Mapping[str, str] | None = None) -> str:
    """Read a prompt template and substitute ``{{NAME}}`` placeholders.

    Templates are read on every call so local prompt edits take effect on the
    next model run without changing Python code or restarting tests.
    """
    template = resources.files(_TEMPLATE_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    content = template.strip()
    for key, value in (variables or {}).items():
        content = content.replace(f"{{{{{key}}}}}", value)
    return content

