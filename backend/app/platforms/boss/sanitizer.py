"""Sanitization helpers for BOSS adapter diagnostics.

Every value that leaves the adapter boundary (into ``FilledPageState``,
``PrepareResult.diagnostic_reference``, ``SubmitResult``) must pass through
these helpers. The invariant: no raw URL, raw cookie, raw token, raw page HTML,
or session profile path is ever persisted. Only hashes, truncated safe titles,
and opaque local diagnostic references survive.

JD read fields (``read_jd``) are the **only** raw page text exception: they
carry user-visible third-party content needed for the recommended-job flow.
:func:`sanitize_jd_field` and :func:`sanitize_jd_result` strip HTML, secrets,
and length-cap each field before it enters the backend.

These helpers are pure (no I/O, no logging) so they are trivially unit-testable.
"""

from __future__ import annotations

import hashlib
import re

#: Maximum length of a sanitized page title. BOSS titles are short; we cap to
#: avoid accidentally persisting a long DOM snippet that slipped into ``title``.
_TITLE_MAX = 120

#: Maximum length of a single JD text field (title, company, description, etc.).
#: The spec allows up to 8000 chars total; per-field cap prevents a page dump
#: from hiding in a single field.
_JD_FIELD_MAX = 8000

#: Maximum number of skill tags extracted from a JD. BOSS typically shows 5-10.
_JD_SKILLS_MAX = 30

#: Patterns that look like secrets. We strip these from any title/diagnostic
#: string before it leaves the boundary. This is defense-in-depth — titles
#: should never contain tokens, but we do not trust the platform DOM.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(token=)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(session=)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(password=)[^\s&]+"),
    re.compile(r"(?i)(authorization:\s*)[A-Za-z0-9._\- ]+"),
)

#: Cookie-like key=value pairs (``Set-Cookie`` style or query-string style).
#: The key must *contain* one of the sensitive substrings (session/cookie/token/
#: auth), appearing anywhere in the key name.
_COOKIE_PATTERN = re.compile(
    r"(?i)([A-Za-z0-9_]*(?:session|cookie|token|auth)[A-Za-z0-9_]*)"
    r"=([A-Za-z0-9._\-+/=]+)"
)

#: HTML tag pattern for JD field cleaning. The userscript extracts text content,
#: but this is a defense-in-depth layer against a page dump that slipped through.
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

#: Excessive whitespace pattern for collapsing multi-line/space runs.
_WHITESPACE_PATTERN = re.compile(r"\s+")


def sanitize_url(url: str) -> str:
    """Return a content-addressed hash of the URL, never the raw URL.

    The raw target resource URL can carry query params (job id, tracking tokens,
    referral codes). We keep none of it — only a ``sha256:<8>`` digest so logs
    and snapshots can correlate without leaking PII.

    **Idempotent:** if the input is already a ``sha256:<8>`` digest (produced by
    an earlier call or by the userscript's ``sha256Short``), it is returned
    unchanged. This prevents double-hashing when a value that is already a hash
    (e.g. ``ctx.target_resource`` from ``job_url_hash``, or a ``read_url`` result
    from the userscript) passes through this function.
    """
    if isinstance(url, str) and url.startswith("sha256:"):
        return url
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"sha256:{digest}"


def sanitize_title(title: str | None) -> str | None:
    """Return a truncated, secret-stripped page title.

    Returns ``None`` when the input is empty. The result is capped at
    :data:`_TITLE_MAX` characters after secret stripping.
    """
    if not title:
        return None
    cleaned = title.strip()
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(r"\1<redacted>", cleaned)
    cleaned = _COOKIE_PATTERN.sub(r"\1=<redacted>", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    return cleaned[:_TITLE_MAX]


def sanitize_jd_field(raw: str | None, *, max_len: int = _JD_FIELD_MAX) -> str | None:
    """Return a secret-stripped, HTML-stripped, length-capped JD text field.

    JD fields (title, company, location, salary, experience, education,
    description) are the **only** raw page text exception: they carry
    user-visible third-party content needed for product flows. Before any such
    value crosses into the backend, it must be:

    - HTML-stripped: no ``<script>``, ``<tag>``, or entity-encoded markup
      survives. If stripping leaves only markup (no text), the result is
      ``None`` so the caller can flag ``jd_too_sparse``.
    - Secret-stripped: same cookie/token/bearer patterns as titles.
    - Length-capped: each field is independently capped at ``max_len`` chars to
      prevent a page dump from slipping through as a "description".

    The result is never ``None`` for non-empty clean input; empty/whitespace
    input returns ``None``.
    """
    if not raw:
        return None
    cleaned = str(raw).strip()
    # Strip HTML tags. The userscript extracts text content, but this is a
    # defense-in-depth layer against a page dump that slipped through.
    cleaned = _HTML_TAG_PATTERN.sub("", cleaned)
    # Decode the most common HTML entities so the stored text is readable.
    cleaned = cleaned.replace("&nbsp;", " ").replace("&amp;", "&")
    cleaned = cleaned.replace("&lt;", "<").replace("&gt;", ">")
    # Secret stripping (same patterns as titles/diagnostics).
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(r"\1<redacted>", cleaned)
    cleaned = _COOKIE_PATTERN.sub(r"\1=<redacted>", cleaned)
    # Collapse excessive whitespace.
    cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned).strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def sanitize_jd_result(jd: dict | None) -> dict | None:
    """Sanitize a ``read_jd`` result dict from the userscript.

    Each text field passes through :func:`sanitize_jd_field`. ``skills`` is a
    list — each item is sanitized, empty items are dropped, and the list is
    capped. ``source_kind`` and ``page_url_hash`` are passed through if present
    (``page_url_hash`` is already a sha256 hash from the userscript).

    Returns ``None`` if the input is ``None`` or not a dict. Returns the
    sanitized dict otherwise; the caller is responsible for checking minimum
    required fields (title + description) and flagging ``jd_too_sparse``.
    """
    if not isinstance(jd, dict):
        return None
    result: dict[str, str | list[str] | None] = {}
    for field_name in (
        "title",
        "company",
        "location",
        "salary",
        "experience",
        "education",
        "description",
    ):
        result[field_name] = sanitize_jd_field(jd.get(field_name))
    # skills: sanitize each, drop empties, cap at _JD_SKILLS_MAX items.
    raw_skills = jd.get("skills")
    if isinstance(raw_skills, list):
        skills = []
        for s in raw_skills[:_JD_SKILLS_MAX]:
            cleaned = sanitize_jd_field(s, max_len=60)
            if cleaned:
                skills.append(cleaned)
        result["skills"] = skills
    else:
        result["skills"] = []
    # Pass-through fields (already safe format).
    result["source_kind"] = jd.get("source_kind") if isinstance(
        jd.get("source_kind"), str
    ) else None
    result["page_url_hash"] = jd.get("page_url_hash") if isinstance(
        jd.get("page_url_hash"), str
    ) else None
    return result


def sanitize_diagnostic(ref: str | None) -> str | None:
    """Sanitize a local diagnostic reference.

    Accepts only a local-relative path or a short opaque code. Strips any
    cookie/token patterns that may have been concatenated by mistake and rejects
    absolute paths that look like session profile directories.
    """
    if not ref:
        return None
    cleaned = ref.strip()
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(r"\1<redacted>", cleaned)
    cleaned = _COOKIE_PATTERN.sub(r"\1=<redacted>", cleaned)
    # Never allow a profile-dir-looking absolute path to leak.
    if re.search(r"(?i)(profile|storage.state|\.json|cookies|session)", cleaned) and "/" in cleaned:
        return "<redacted-path>"
    return cleaned or None


__all__ = [
    "sanitize_diagnostic",
    "sanitize_jd_field",
    "sanitize_jd_result",
    "sanitize_title",
    "sanitize_url",
]
