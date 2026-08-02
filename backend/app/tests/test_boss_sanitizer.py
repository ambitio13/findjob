"""Unit tests for the BOSS adapter sanitizer helpers.

The sanitizer is the last line of defense before values leave the adapter
boundary. These tests prove no raw URL, cookie, token, or session path can
survive sanitization.
"""

from __future__ import annotations

from app.platforms.boss.sanitizer import (
    sanitize_diagnostic,
    sanitize_jd_field,
    sanitize_jd_result,
    sanitize_title,
    sanitize_url,
)

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


# ---------------------------------------------------------------------------
# sanitize_jd_field
# ---------------------------------------------------------------------------


def test_sanitize_jd_field_passes_through_safe_text() -> None:
    assert sanitize_jd_field("高级前端工程师") == "高级前端工程师"
    assert sanitize_jd_field("React, TypeScript, Node.js") == "React, TypeScript, Node.js"


def test_sanitize_jd_field_returns_none_for_empty() -> None:
    assert sanitize_jd_field("") is None
    assert sanitize_jd_field(None) is None
    assert sanitize_jd_field("   ") is None


def test_sanitize_jd_field_strips_html_tags() -> None:
    raw = "<span>高级前端工程师</span>"
    result = sanitize_jd_field(raw)
    assert result == "高级前端工程师"


def test_sanitize_jd_field_strips_html_tags_leaving_no_text_returns_none() -> None:
    # If the field is pure markup with no text content, the result is None
    # so the caller can flag jd_too_sparse.
    assert sanitize_jd_field("<div></div>") is None
    assert sanitize_jd_field("<br/><hr/>") is None


def test_sanitize_jd_field_strips_script_tags() -> None:
    raw = "<script>alert('xss')</script>高级前端工程师"
    result = sanitize_jd_field(raw)
    assert result is not None
    # HTML tags are stripped; text content between tags survives.
    assert "<script>" not in result
    assert "</script>" not in result
    assert "高级前端工程师" in result


def test_sanitize_jd_field_decodes_html_entities() -> None:
    raw = "React&nbsp;&amp;&nbsp;TypeScript &lt;Node.js&gt;"
    result = sanitize_jd_field(raw)
    assert result is not None
    assert "&nbsp;" not in result
    assert "&amp;" not in result
    assert "&lt;" not in result
    assert "&gt;" not in result
    assert "React" in result
    assert "TypeScript" in result


def test_sanitize_jd_field_strips_bearer_token() -> None:
    result = sanitize_jd_field("description bearer abc123secrettoken")
    assert result is not None
    assert "abc123secrettoken" not in result
    assert "<redacted>" in result


def test_sanitize_jd_field_strips_cookie_session_pairs() -> None:
    result = sanitize_jd_field("job?sessionid=abc123&cookie=xyz")
    assert result is not None
    assert "abc123" not in result
    assert "xyz" not in result


def test_sanitize_jd_field_strips_password_query() -> None:
    result = sanitize_jd_field("login?password=hunter2")
    assert result is not None
    assert "hunter2" not in result


def test_sanitize_jd_field_caps_length() -> None:
    long_text = "A" * 20000
    result = sanitize_jd_field(long_text)
    assert result is not None
    assert len(result) <= 8000


def test_sanitize_jd_field_custom_max_len() -> None:
    long_text = "A" * 200
    result = sanitize_jd_field(long_text, max_len=50)
    assert result is not None
    assert len(result) <= 50


def test_sanitize_jd_field_collapses_whitespace() -> None:
    raw = "  React\n\n\n   TypeScript   "
    result = sanitize_jd_field(raw)
    assert result == "React TypeScript"


# ---------------------------------------------------------------------------
# sanitize_jd_result
# ---------------------------------------------------------------------------


def test_sanitize_jd_result_returns_none_for_none() -> None:
    assert sanitize_jd_result(None) is None


def test_sanitize_jd_result_returns_none_for_non_dict() -> None:
    assert sanitize_jd_result("not a dict") is None
    assert sanitize_jd_result(42) is None
    assert sanitize_jd_result([]) is None


def test_sanitize_jd_result_sanitizes_all_text_fields() -> None:
    jd = {
        "title": "<span>高级前端工程师</span>",
        "company": "某科技公司",
        "location": "北京",
        "salary": "25-50K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "description": "<p>负责前端开发</p>",
        "skills": ["React", "TypeScript"],
        "source_kind": "boss_recommended_job",
        "page_url_hash": "sha256:abcdef12",
    }
    result = sanitize_jd_result(jd)
    assert result is not None
    assert result["title"] == "高级前端工程师"
    assert result["company"] == "某科技公司"
    assert result["location"] == "北京"
    assert result["salary"] == "25-50K·14薪"
    assert result["experience"] == "3-5年"
    assert result["education"] == "本科"
    assert result["description"] == "负责前端开发"
    assert result["skills"] == ["React", "TypeScript"]
    assert result["source_kind"] == "boss_recommended_job"
    assert result["page_url_hash"] == "sha256:abcdef12"


def test_sanitize_jd_result_handles_missing_fields() -> None:
    jd = {"title": "工程师", "description": "描述"}
    result = sanitize_jd_result(jd)
    assert result is not None
    assert result["title"] == "工程师"
    assert result["description"] == "描述"
    assert result["company"] is None
    assert result["location"] is None
    assert result["skills"] == []
    assert result["source_kind"] is None
    assert result["page_url_hash"] is None


def test_sanitize_jd_result_strips_html_in_all_fields() -> None:
    jd = {
        "title": "<b>工程师</b>",
        "company": "<i>公司</i>",
        "description": "<div>描述<script>alert(1)</script></div>",
    }
    result = sanitize_jd_result(jd)
    assert result is not None
    assert result["title"] == "工程师"
    assert result["company"] == "公司"
    assert result["description"] == "描述alert(1)"
    assert "script" not in (result["description"] or "")


def test_sanitize_jd_result_strips_secrets_in_description() -> None:
    jd = {
        "title": "工程师",
        "description": "contact via token=secret123 for details",
    }
    result = sanitize_jd_result(jd)
    assert result is not None
    assert "secret123" not in (result["description"] or "")
    assert "<redacted>" in (result["description"] or "")


def test_sanitize_jd_result_caps_skills_list() -> None:
    jd = {
        "title": "工程师",
        "description": "描述",
        "skills": [f"skill_{i}" for i in range(50)],
    }
    result = sanitize_jd_result(jd)
    assert result is not None
    assert len(result["skills"]) <= 30


def test_sanitize_jd_result_drops_empty_skills() -> None:
    jd = {
        "title": "工程师",
        "description": "描述",
        "skills": ["React", "", "  ", None, "TypeScript"],
    }
    result = sanitize_jd_result(jd)
    assert result is not None
    assert result["skills"] == ["React", "TypeScript"]


def test_sanitize_jd_result_strips_html_in_skills() -> None:
    jd = {
        "title": "工程师",
        "description": "描述",
        "skills": ["<b>React</b>", "TypeScript"],
    }
    result = sanitize_jd_result(jd)
    assert result is not None
    assert result["skills"] == ["React", "TypeScript"]


def test_sanitize_jd_result_skills_not_list_returns_empty() -> None:
    jd = {
        "title": "工程师",
        "description": "描述",
        "skills": "React, TypeScript",
    }
    result = sanitize_jd_result(jd)
    assert result is not None
    assert result["skills"] == []


def test_sanitize_jd_result_passes_through_source_kind_only_if_str() -> None:
    jd = {
        "title": "工程师",
        "description": "描述",
        "source_kind": 123,
    }
    result = sanitize_jd_result(jd)
    assert result is not None
    assert result["source_kind"] is None


def test_sanitize_jd_result_rejects_page_dump_in_description() -> None:
    # A full HTML page dump in description should be stripped to text only.
    page_dump = (
        "<html><head><title>Page</title></head>"
        "<body><div>职位描述</div></body></html>"
    )
    jd = {"title": "工程师", "description": page_dump}
    result = sanitize_jd_result(jd)
    assert result is not None
    assert "<html>" not in (result["description"] or "")
    assert "<head>" not in (result["description"] or "")
    assert "职位描述" in (result["description"] or "")
