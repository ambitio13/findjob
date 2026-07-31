"""Tests for the resume upload + parsing foundation (design §7).

Covers upload success for txt/pdf/docx, the accepted-but-unparsed ``.rtf``
path, rejected ``.html``, oversized-file rejection, user-scoped listing,
cross-user 404, version listing without ``raw_text``, re-upload creating a
fresh Resume v1, and the guarantee that ``raw_text`` never appears in logs.

Binary fixtures are generated inline (a minimal hand-written PDF and a
``python-docx`` document) to avoid committing binary files.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Inline binary fixtures
# ---------------------------------------------------------------------------


def _txt_bytes() -> bytes:
    return "张三\nPython 后端工程师\n5 年经验".encode()


def _pdf_bytes() -> bytes:
    """Return a minimal single-page PDF whose text is 'Hello Resume'."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length 44 >>\nstream\nBT /F1 24 Tf 100 700 Td "
        b"(Hello Resume) Tj ET\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000266 00000 n \n"
        b"0000000360 00000 n \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n434\n%%EOF"
    )


def _docx_bytes() -> bytes:
    """Return a minimal .docx whose paragraph text is 'Hello Resume'."""
    import docx

    buf = io.BytesIO()
    document = docx.Document()
    document.add_paragraph("Hello Resume")
    document.save(buf)
    return buf.getvalue()


def _upload(client: TestClient, filename: str, content: bytes, user: str | None = None):
    """POST a file to /resumes and return the response."""
    headers = {"X-User-Id": user} if user else None
    return client.post(
        "/api/v1/resumes",
        files={"file": (filename, content, "application/octet-stream")},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# 1-3: upload success for txt / pdf / docx
# ---------------------------------------------------------------------------


def test_upload_txt_creates_resume_and_version(client: TestClient) -> None:
    # Test 1: .txt -> 201, raw_text persisted, version_no=1.
    resp = _upload(client, "resume.txt", _txt_bytes(), user="u_txt")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "resume.txt"
    latest = body["latest_version"]
    assert latest["version_no"] == 1
    assert "张三" in latest["raw_text"]
    facts = latest["parsed_facts"]
    assert facts["_parser"] == "text"
    assert facts["_parser_status"] == "parsed"


def test_upload_pdf_extracts_text(client: TestClient) -> None:
    # Test 2: .pdf -> 201, raw_text non-empty.
    resp = _upload(client, "resume.pdf", _pdf_bytes(), user="u_pdf")
    assert resp.status_code == 201, resp.text
    latest = resp.json()["latest_version"]
    assert "Hello Resume" in latest["raw_text"]
    assert latest["parsed_facts"]["_parser"] == "pdfplumber"


def test_upload_docx_extracts_text(client: TestClient) -> None:
    # Test 3: .docx -> 201, raw_text non-empty.
    resp = _upload(client, "resume.docx", _docx_bytes(), user="u_docx")
    assert resp.status_code == 201, resp.text
    latest = resp.json()["latest_version"]
    assert "Hello Resume" in latest["raw_text"]
    assert latest["parsed_facts"]["_parser"] == "python-docx"


# ---------------------------------------------------------------------------
# 4-5: accepted-but-unparsed and rejected extensions
# ---------------------------------------------------------------------------


def test_upload_rtf_is_accepted_but_unsupported(client: TestClient) -> None:
    # Test 4: .rtf is accepted (201) but marked unsupported; no invented facts.
    resp = _upload(client, "resume.rtf", b"{\\rtf1 legacy}", user="u_rtf")
    assert resp.status_code == 201, resp.text
    latest = resp.json()["latest_version"]
    assert latest["raw_text"] == ""
    facts = latest["parsed_facts"]
    assert facts["_parser_status"] == "unsupported"
    # Only parser telemetry + extraction status keys are present; no resume
    # facts are invented.
    assert set(facts.keys()) <= {"_parser", "_parser_status", "_extraction"}
    assert facts["_extraction"]["status"] == "not_run"


def test_upload_html_is_rejected(client: TestClient) -> None:
    # Test 5: .html is not a resume extension -> 422.
    resp = _upload(client, "resume.html", b"<html></html>", user="u_html")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 6: size limit
# ---------------------------------------------------------------------------


def test_oversized_upload_returns_413_and_creates_nothing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
) -> None:
    # Test 6: an upload exceeding resume_max_size_mb -> 413 and no DB rows/files.
    import app.api.v1.resumes as resumes_mod
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "resume_max_size_mb", 1)
    monkeypatch.setattr(resumes_mod, "get_settings", lambda: settings)

    big = b"x" * (2 * 1024 * 1024)
    resp = _upload(client, "big.txt", big, user="u_big")
    assert resp.status_code == 413

    # No resume row should exist for this user.
    listing = client.get("/api/v1/resumes", headers={"X-User-Id": "u_big"})
    assert listing.status_code == 200
    assert listing.json()["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# 7-8: user scoping
# ---------------------------------------------------------------------------


def test_list_resumes_only_returns_current_user(client: TestClient) -> None:
    # Test 7: GET /resumes lists only the current user's resumes.
    a = _upload(client, "a.txt", _txt_bytes(), user="u_a")
    b = _upload(client, "b.txt", _txt_bytes(), user="u_b")
    assert a.status_code == 201 and b.status_code == 201

    a_list = client.get("/api/v1/resumes", headers={"X-User-Id": "u_a"})
    assert a_list.status_code == 200
    ids = {item["id"] for item in a_list.json()["items"]}
    assert a.json()["id"] in ids
    assert b.json()["id"] not in ids


def test_get_resume_detail_404_for_other_user(client: TestClient) -> None:
    # Test 8: GET /resumes/{id} returns 404 for another user's resume.
    owner = _upload(client, "secret.txt", _txt_bytes(), user="owner_only")
    assert owner.status_code == 201
    resume_id = owner.json()["id"]

    owner_view = client.get(f"/api/v1/resumes/{resume_id}", headers={"X-User-Id": "owner_only"})
    assert owner_view.status_code == 200

    intruder = client.get(f"/api/v1/resumes/{resume_id}", headers={"X-User-Id": "intruder"})
    assert intruder.status_code == 404

    # demo_user (no header) also gets 404.
    default = client.get(f"/api/v1/resumes/{resume_id}")
    assert default.status_code == 404


# ---------------------------------------------------------------------------
# 9: versions listing
# ---------------------------------------------------------------------------


def test_list_versions_omits_raw_text(client: TestClient) -> None:
    # Test 9: GET /resumes/{id}/versions returns version list without raw_text.
    upload = _upload(client, "resume.txt", _txt_bytes(), user="u_versions")
    assert upload.status_code == 201
    resume_id = upload.json()["id"]

    resp = client.get(f"/api/v1/resumes/{resume_id}/versions", headers={"X-User-Id": "u_versions"})
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) == 1
    v = versions[0]
    assert v["version_no"] == 1
    assert v["parser_status"] == "parsed"
    assert v["parser_name"] == "text"
    assert "raw_text" not in v


def test_filename_display_and_storage_uri_are_sanitized(client: TestClient) -> None:
    resp = _upload(
        client,
        "../weird name.txt",
        _txt_bytes(),
        user="../unsafe/user",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "weird name.txt"
    storage_uri = body["storage_uri"]
    assert ".." not in storage_uri
    assert " " not in storage_uri
    assert storage_uri.endswith("/weird_name.txt")


def test_same_filename_uploads_do_not_collide(client: TestClient) -> None:
    first = _upload(client, "same.txt", b"first", user="same_user")
    second = _upload(client, "same.txt", b"second", user="same_user")
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["storage_uri"] != second.json()["storage_uri"]
    assert first.json()["filename"] == "same.txt"
    assert second.json()["filename"] == "same.txt"


def test_unicode_filename_keeps_safe_storage_extension(client: TestClient) -> None:
    resp = _upload(client, "张三简历.pdf", _pdf_bytes(), user="unicode_user")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "张三简历.pdf"
    assert body["storage_uri"].endswith("/resume.pdf")


# ---------------------------------------------------------------------------
# 10: re-upload is a new Resume (version_no=1 each)
# ---------------------------------------------------------------------------


def test_reupload_creates_new_resume_with_version_one(client: TestClient) -> None:
    # Test 10: each upload is a brand-new Resume whose first version is v1.
    first = _upload(client, "r1.txt", _txt_bytes(), user="u_reup")
    assert first.status_code == 201
    second = _upload(client, "r2.txt", _txt_bytes(), user="u_reup")
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["latest_version"]["version_no"] == 1
    assert second.json()["latest_version"]["version_no"] == 1

    listing = client.get("/api/v1/resumes", headers={"X-User-Id": "u_reup"})
    assert listing.json()["meta"]["total"] == 2


# ---------------------------------------------------------------------------
# 11: raw_text never appears in logs
# ---------------------------------------------------------------------------


def test_raw_text_never_appears_in_logs(
    client: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Test 11: raw_text must never appear in log output; only lengths are logged.
    # structlog emits to stdout via PrintLoggerFactory (not the stdlib handlers
    # that caplog attaches to), so we capture stdout directly.
    secret_marker = "SUPER_SECRET_RESUME_TOKEN"
    content = f"name: {secret_marker}\nrole: backend".encode()
    resp = _upload(client, "secret.txt", content, user="u_log")
    assert resp.status_code == 201

    captured = capsys.readouterr().out
    assert secret_marker not in captured
    # The upload log line should mention raw_text_len (a length), not the text.
    assert "raw_text_len" in captured


# ---------------------------------------------------------------------------
# 12-15: structured fact extraction on upload + re-extract
# ---------------------------------------------------------------------------


def _extraction_status(client: TestClient, resume_id: str, user: str) -> str:
    """Read the latest version's _extraction.status via the detail endpoint."""
    resp = client.get(f"/api/v1/resumes/{resume_id}", headers={"X-User-Id": user})
    assert resp.status_code == 200, resp.text
    return resp.json()["latest_version"]["parsed_facts"]["_extraction"]["status"]


def _wait_for_terminal_extraction(
    client: TestClient, resume_id: str, user: str, *, timeout: float = 5.0
) -> str:
    """Poll the detail endpoint until _extraction.status reaches a terminal state.

    FastAPI ``BackgroundTasks`` run synchronously inside ``TestClient`` after the
    response is sent, so the first response already reflects the terminal status
    in most cases. This helper guards against any scheduling ordering by polling
    a few times before giving up.
    """
    import time

    terminal = {"succeeded", "failed", "needs_confirmation", "not_run"}
    deadline = time.monotonic() + timeout
    last = _extraction_status(client, resume_id, user)
    while last not in terminal and time.monotonic() < deadline:
        time.sleep(0.05)
        last = _extraction_status(client, resume_id, user)
    assert last in terminal, f"extraction never reached terminal state: {last}"
    return last


def test_upload_txt_returns_pending_then_succeeds(client: TestClient) -> None:
    # Test 12: .txt upload returns immediately with _extraction.status=pending
    # (or running, since the background task may start before serialization),
    # and eventually reaches succeeded with typed facts.
    resp = _upload(client, "resume.txt", _txt_bytes(), user="u_extract")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    resume_id = body["id"]
    latest = body["latest_version"]
    initial_status = latest["parsed_facts"]["_extraction"]["status"]
    # Upload must not block on extraction: the response shows a non-terminal
    # status, or the background task already finished (still acceptable since
    # the HTTP request did not wait on the model call inline).
    assert initial_status in {"pending", "running", "succeeded"}, initial_status

    final = _wait_for_terminal_extraction(client, resume_id, "u_extract")
    assert final == "succeeded"

    detail = client.get(f"/api/v1/resumes/{resume_id}", headers={"X-User-Id": "u_extract"}).json()[
        "latest_version"
    ]
    facts = detail["parsed_facts"]
    assert "run_id" in facts["_extraction"]
    assert facts["facts"]["contact"]["name"] == "张三"
    assert "Python" in facts["facts"]["skills"]


def test_upload_rtf_skips_extraction_not_run(client: TestClient) -> None:
    # Test 13: .rtf upload -> _extraction.status=not_run, no facts key.
    resp = _upload(client, "resume.rtf", b"{\\rtf1 legacy}", user="u_notrun")
    assert resp.status_code == 201
    facts = resp.json()["latest_version"]["parsed_facts"]
    assert facts["_extraction"]["status"] == "not_run"
    assert "facts" not in facts


def test_reextract_refreshes_facts(client: TestClient) -> None:
    # Test 14: POST /resumes/{id}/versions/{vid}/extract re-runs extraction
    # synchronously (the explicit re-extract path stays inline).
    upload = _upload(client, "resume.txt", _txt_bytes(), user="u_reextract")
    assert upload.status_code == 201
    body = upload.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]

    # Wait for the automatic background extraction to finish so we can compare.
    _wait_for_terminal_extraction(client, resume_id, "u_reextract")
    before = client.get(
        f"/api/v1/resumes/{resume_id}", headers={"X-User-Id": "u_reextract"}
    ).json()["latest_version"]["parsed_facts"]["_extraction"]["run_id"]

    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/extract",
        headers={"X-User-Id": "u_reextract"},
    )
    assert resp.status_code == 200, resp.text
    facts = resp.json()["latest_version"]["parsed_facts"]
    assert facts["_extraction"]["status"] == "succeeded"
    # A fresh run was created.
    assert facts["_extraction"]["run_id"] != before
    assert facts["facts"]["contact"]["name"] == "张三"


def test_reextract_404_for_other_user(client: TestClient) -> None:
    # Test 15: re-extract is user-scoped; cross-user returns 404.
    upload = _upload(client, "resume.txt", _txt_bytes(), user="owner_re")
    assert upload.status_code == 201
    body = upload.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]

    intruder = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/extract",
        headers={"X-User-Id": "intruder_re"},
    )
    assert intruder.status_code == 404


def test_extraction_secret_not_in_agent_run_result(client: TestClient) -> None:
    # Test 16: the secret resume token must not leak into AgentRun/Step rows.
    secret_marker = "SUPER_SECRET_RESUME_TOKEN"
    content = f"name: {secret_marker}\nPython 5年".encode()
    resp = _upload(client, "secret.txt", content, user="u_leak")
    assert resp.status_code == 201
    resume_id = resp.json()["id"]
    _wait_for_terminal_extraction(client, resume_id, "u_leak")

    detail = client.get(f"/api/v1/resumes/{resume_id}", headers={"X-User-Id": "u_leak"}).json()[
        "latest_version"
    ]
    run_id = detail["parsed_facts"]["_extraction"]["run_id"]

    # Fetch the run + steps via the agent-runs API and assert no leakage.
    run_resp = client.get(
        f"/api/v1/agent-runs/{run_id}",
        headers={"X-User-Id": "u_leak"},
    )
    assert run_resp.status_code == 200
    blob = run_resp.json()
    import json as _json

    assert secret_marker not in _json.dumps(blob, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 17: upload with a failing gateway still returns 201 + _extraction.status=failed
# ---------------------------------------------------------------------------


def test_upload_extraction_failure_still_returns_201(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model failure during background extraction must not break the upload.

    The upload contract (design §4) says: when extraction fails during upload,
    the resume + raw_text are already saved, ``_extraction.status="failed"``
    is persisted, and the upload still returns 201. Because extraction now runs
    as a background task, the response may show ``pending``/``running``; we then
    poll until the terminal ``failed`` status and assert the failed run is
    auditable.
    """
    import json as _json

    from app.api.deps import get_model_gateway_dep
    from app.main import app
    from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway

    class _RaisingGateway(ModelGateway):
        """Gateway that raises on every chat() call."""

        provider_name = "raising-test"

        async def chat(self, request: ChatRequest) -> ChatResponse:
            raise RuntimeError("simulated provider outage")

    stub = _RaisingGateway()
    app.dependency_overrides[get_model_gateway_dep] = lambda: stub
    try:
        resp = _upload(client, "resume.txt", _txt_bytes(), user="u_fail_extract")
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)

    # Upload itself must succeed — the file was saved and raw_text extracted.
    assert resp.status_code == 201, resp.text
    body = resp.json()
    resume_id = body["id"]
    latest = body["latest_version"]
    assert latest["version_no"] == 1
    assert "张三" in latest["raw_text"]

    # The upload response must not block on extraction; the status is
    # non-terminal (pending/running) or already failed.
    initial = latest["parsed_facts"]["_extraction"]["status"]
    assert initial in {"pending", "running", "failed"}, initial

    # Poll until the background runner persists the terminal failed status.
    final = _wait_for_terminal_extraction(client, resume_id, "u_fail_extract")
    assert final == "failed"

    detail = client.get(
        f"/api/v1/resumes/{resume_id}", headers={"X-User-Id": "u_fail_extract"}
    ).json()["latest_version"]
    facts = detail["parsed_facts"]
    assert facts["_extraction"]["status"] == "failed"
    assert "run_id" in facts["_extraction"]
    # No typed facts written on failure.
    assert "facts" not in facts

    # The failed AgentRun is persisted and auditable via the agent-runs API.
    run_id = facts["_extraction"]["run_id"]
    run_resp = client.get(
        f"/api/v1/agent-runs/{run_id}",
        headers={"X-User-Id": "u_fail_extract"},
    )
    assert run_resp.status_code == 200
    run_blob = run_resp.json()
    assert run_blob["status"] == "failed"
    assert run_blob["workflow_type"] == "resume_fact_extraction"

    # Resume content must never leak into the run result, even on failure.
    assert "张三" not in _json.dumps(run_blob, ensure_ascii=False)
