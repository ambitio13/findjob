"""Sanitization helpers for BOSS adapter diagnostics.

Every value that leaves the adapter boundary (into ``FilledPageState``,
``PrepareResult.diagnostic_reference``, ``SubmitResult``) must pass through
these helpers. The invariant: no raw URL, raw cookie, raw token, raw page HTML,
or session profile path is ever persisted. Only hashes, truncated safe titles,
and opaque local diagnostic references survive.

These helpers are pure (no I/O, no logging) so they are trivially unit-testable.
"""

from __future__ import annotations

import hashlib
import re

#: Maximum length of a sanitized page title. BOSS titles are short; we cap to
#: avoid accidentally persisting a long DOM snippet that slipped into ``title``.
_TITLE_MAX = 120

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


def sanitize_url(url: str) -> str:
    """Return a content-addressed hash of the URL, never the raw URL.

    The raw target resource URL can carry query params (job id, tracking tokens,
    referral codes). We keep none of it — only a ``sha256:<8>`` digest so logs
    and snapshots can correlate without leaking PII.
    """
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


__all__ = ["sanitize_diagnostic", "sanitize_title", "sanitize_url"]
