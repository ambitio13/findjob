"""Resume parser adapter.

Extracts plain text from uploaded resume files. Supports ``.txt``, ``.pdf``,
and ``.docx``; other formats yield an explicit ``unsupported`` result. This
module never invents resume facts: ``parsed_facts`` stores parser telemetry
only (``_parser`` / ``_parser_status``), while structured extraction is
deferred to a later task.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.logging import get_logger

_log = get_logger("app.services.resume_parser")

ParseStatus = Literal["parsed", "unsupported"]
ParserName = Literal["text", "pdfplumber", "python-docx", "unsupported"]

# Extension allowlist (lowercase, without dot). Declared here so the router
# and tests can reuse it.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({"txt", "pdf", "docx"})

# All accepted resume-like extensions. The first three are fully parsed; the
# rest are accepted at upload (201) but marked ``unsupported`` because we lack
# a parser for them in the MVP. This keeps the upload contract honest: the
# user can store a ``.doc``/``.rtf`` resume, but no text is extracted yet.
ACCEPTED_EXTENSIONS: frozenset[str] = frozenset({"txt", "pdf", "docx", "doc", "rtf"})


@dataclass
class ParseResult:
    """Outcome of parsing a single uploaded resume file."""

    raw_text: str
    parsed_facts: dict[str, Any] = field(default_factory=dict)
    status: ParseStatus = "parsed"
    parser_name: ParserName = "text"

    def to_facts(self) -> dict[str, Any]:
        """Return ``parsed_facts`` augmented with parser telemetry.

        Telemetry keys are namespaced with a leading underscore so they are
        clearly not resume facts. The router stores this in
        ``ResumeVersion.parsed_facts``.
        """
        facts: dict[str, Any] = dict(self.parsed_facts)
        facts["_parser"] = self.parser_name
        facts["_parser_status"] = self.status
        return facts


def _extract_text(content: bytes) -> str:
    """Decode plain-text bytes, preferring utf-8 and falling back to latin-1."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _extract_pdf(content: bytes) -> str:
    import pdfplumber

    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts).strip()


def _extract_docx(content: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(content))
    return "\n".join(p.text for p in document.paragraphs).strip()


def parse_resume(content: bytes, mime_type: str | None, filename: str) -> ParseResult:
    """Parse ``content`` according to the extension of ``filename``.

    ``mime_type`` is accepted for interface completeness but is not trusted for
    dispatch (client-declared MIME can be spoofed); the extension is the
    authoritative signal for the MVP.
    """
    ext = _extension(filename)

    if ext == "txt":
        return ParseResult(
            raw_text=_extract_text(content),
            parser_name="text",
            status="parsed",
        )
    if ext == "pdf":
        try:
            return ParseResult(
                raw_text=_extract_pdf(content),
                parser_name="pdfplumber",
                status="parsed",
            )
        except Exception as exc:  # noqa: BLE001 — surface any parse failure as 422
            _log.info("resume.parse_failed", ext=ext, error_type=type(exc).__name__)
            raise
    if ext == "docx":
        try:
            return ParseResult(
                raw_text=_extract_docx(content),
                parser_name="python-docx",
                status="parsed",
            )
        except Exception as exc:  # noqa: BLE001 — surface any parse failure as 422
            _log.info("resume.parse_failed", ext=ext, error_type=type(exc).__name__)
            raise

    # Unsupported extension: explicit empty result, never invented facts.
    return ParseResult(
        raw_text="",
        parsed_facts={},
        status="unsupported",
        parser_name="unsupported",
    )


def _extension(filename: str) -> str:
    """Return the lowercase extension of ``filename`` without the dot."""
    dot = filename.rfind(".")
    if dot < 0:
        return ""
    return filename[dot + 1 :].lower()
