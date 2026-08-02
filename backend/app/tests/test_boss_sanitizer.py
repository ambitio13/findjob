"""Unit tests for the BOSS adapter sanitizer helpers.

The sanitizer is the last line of defense before values leave the adapter
boundary. These tests prove no raw URL, cookie, token, or session path can
survive sanitization.
"""

from __future__ import annotations

from app.platforms.boss.sanitizer import sanitize_diagnostic, sanitize_title, sanitize_url

# ---------------------------------------------------------------------------
# sanitize_url
# ---------------------------------------------------------------------------


def test_sanitize_url_returns_sha256_prefix_not_raw_url() -> None:
    raw = "https://www.zhipin.com/job/123?ref=abc&token=secret"
    hashed = sanitize_url(raw)
    assert hashed.startswith("sha256:")
    assert len(hashed) == len("sha256:") + 8
    assert "zhipin" not in hashed
    assert "token" not in hashed
    assert "secret" not in hashed


def test_sanitize_url_is_deterministic() -> None:
    assert sanitize_url("https://example.com/a") == sanitize_url("https://example.com/a")


def test_sanitize_url_distinguishes_different_urls() -> None:
    assert sanitize_url("https://a.com") != sanitize_url("https://b.com")


# ---------------------------------------------------------------------------
# sanitize_title
# ---------------------------------------------------------------------------


def test_sanitize_title_passes_through_safe_title() -> None:
    assert sanitize_title("BOSS直聘 - 职位详情") == "BOSS直聘 - 职位详情"


def test_sanitize_title_returns_none_for_empty() -> None:
    assert sanitize_title("") is None
    assert sanitize_title(None) is None
    assert sanitize_title("   ") is None


def test_sanitize_title_truncates_long_title() -> None:
    long_title = "A" * 500
    result = sanitize_title(long_title)
    assert result is not None
    assert len(result) <= 120


def test_sanitize_title_strips_bearer_token() -> None:
    result = sanitize_title("Page - bearer abc123secrettoken")
    assert result is not None
    assert "abc123secrettoken" not in result
    assert "<redacted>" in result


def test_sanitize_title_strips_cookie_session_pairs() -> None:
    result = sanitize_title("job?sessionid=abc123&cookie=xyz")
    assert result is not None
    assert "abc123" not in result
    assert "xyz" not in result


def test_sanitize_title_strips_password_query() -> None:
    result = sanitize_title("login?password=hunter2")
    assert result is not None
    assert "hunter2" not in result


# ---------------------------------------------------------------------------
# sanitize_diagnostic
# ---------------------------------------------------------------------------


def test_sanitize_diagnostic_passes_through_safe_code() -> None:
    assert sanitize_diagnostic("login_marker") == "login_marker"
    assert sanitize_diagnostic("missing_form_anchors") == "missing_form_anchors"


def test_sanitize_diagnostic_returns_none_for_empty() -> None:
    assert sanitize_diagnostic("") is None
    assert sanitize_diagnostic(None) is None


def test_sanitize_diagnostic_strips_token_patterns() -> None:
    result = sanitize_diagnostic("error near token=secret123")
    assert result is not None
    assert "secret123" not in result


def test_sanitize_diagnostic_redacts_profile_path() -> None:
    result = sanitize_diagnostic("/home/user/.boss-profile/session.json")
    assert result is not None
    assert ".boss-profile" not in result
    assert "session.json" not in result
