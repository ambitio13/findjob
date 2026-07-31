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
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.models import AgentRun
from app.db.session import SessionLocal

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


def _fake_enqueue_pool() -> Any:
    """Return a fake arq pool whose ``enqueue_job`` succeeds without Redis."""
    pool = AsyncMock()
    pool.enqueue_job.return_value = object()
    return pool


def _patch_get_queue_ok() -> Any:
    """Patch ``get_queue`` to return a fake pool (no real Redis needed).

    Extraction is now async: uploads with raw_text enqueue a
    ``ResumeFactExtractionPayload``. Tests that don't care about the extraction
    result still need to suppress the real Redis call.
    """
    return patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(return_value=_fake_enqueue_pool()),
    )


# ---------------------------------------------------------------------------
# 1-3: upload success for txt / pdf / docx
# ---------------------------------------------------------------------------


def test_upload_txt_creates_resume_and_version(client: TestClient) -> None:
    # Test 1: .txt -> 201, raw_text persisted, version_no=1.
    with _patch_get_queue_ok():
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
    with _patch_get_queue_ok():
        resp = _upload(client, "resume.pdf", _pdf_bytes(), user="u_pdf")
    assert resp.status_code == 201, resp.text
    latest = resp.json()["latest_version"]
    assert "Hello Resume" in latest["raw_text"]
    assert latest["parsed_facts"]["_parser"] == "pdfplumber"


def test_upload_docx_extracts_text(client: TestClient) -> None:
    # Test 3: .docx -> 201, raw_text non-empty.
    with _patch_get_queue_ok():
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
    with _patch_get_queue_ok():
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
    with _patch_get_queue_ok():
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
    with _patch_get_queue_ok():
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
    with _patch_get_queue_ok():
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
    with _patch_get_queue_ok():
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
    with _patch_get_queue_ok():
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
    with _patch_get_queue_ok():
        resp = _upload(client, "secret.txt", content, user="u_log")
    assert resp.status_code == 201

    captured = capsys.readouterr().out
    assert secret_marker not in captured
    # The upload log line should mention raw_text_len (a length), not the text.
    assert "raw_text_len" in captured


# ---------------------------------------------------------------------------
# 12-17: structured fact extraction on upload + re-extract
#
# Extraction is now an asynchronous queue workflow (08-01-async-resume-fact-
# extraction). The upload endpoint creates a queued AgentRun, marks
# _extraction.status=pending, enqueues a ResumeFactExtractionPayload, and
# returns immediately. These API-layer tests patch ``get_queue`` so no real
# Redis is required. The full worker handler execution path is covered in
# ``test_resume_extraction_api.py``.
# ---------------------------------------------------------------------------


def test_upload_txt_returns_pending_with_queued_run(client: TestClient) -> None:
    # Test 12: .txt upload enqueues extraction and returns immediately with
    # _extraction.status=pending and a queued AgentRun.
    with _patch_get_queue_ok():
        resp = _upload(client, "resume.txt", _txt_bytes(), user="u_extract")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    latest = body["latest_version"]
    extraction = latest["parsed_facts"]["_extraction"]
    # Upload must not block on extraction: status is pending (non-terminal).
    assert extraction["status"] == "pending", extraction["status"]
    assert "run_id" in extraction
    run_id = extraction["run_id"]

    # DB: a queued AgentRun persisted with sanitized metadata.
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "queued"
        assert run.workflow_type == "resume_fact_extraction"
        assert run.user_id == "u_extract"
        assert run.result["raw_text_len"] == len(_txt_bytes().decode())


def test_upload_rtf_skips_extraction_not_run(client: TestClient) -> None:
    # Test 13: .rtf upload -> _extraction.status=not_run, no facts key, no run.
    with _patch_get_queue_ok() as mock_queue:
        resp = _upload(client, "resume.rtf", b"{\\rtf1 legacy}", user="u_notrun")
    assert resp.status_code == 201
    facts = resp.json()["latest_version"]["parsed_facts"]
    assert facts["_extraction"]["status"] == "not_run"
    assert "facts" not in facts
    assert "run_id" not in (facts["_extraction"] or {})
    # No enqueue attempted for unsupported format.
    mock_queue.assert_not_called()

    with SessionLocal() as db:
        rows = db.execute(select(AgentRun).where(AgentRun.user_id == "u_notrun")).scalars().all()
        assert len(rows) == 0


def test_reextract_enqueues_fresh_run_returns_202(client: TestClient) -> None:
    # Test 14: POST /resumes/{id}/versions/{vid}/extract now enqueues a fresh
    # run and returns immediately with status=pending (HTTP 202).
    with _patch_get_queue_ok():
        upload = _upload(client, "resume.txt", _txt_bytes(), user="u_reextract")
    assert upload.status_code == 201
    body = upload.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]
    first_run_id = body["latest_version"]["parsed_facts"]["_extraction"]["run_id"]

    with _patch_get_queue_ok():
        resp = client.post(
            f"/api/v1/resumes/{resume_id}/versions/{version_id}/extract",
            headers={"X-User-Id": "u_reextract"},
        )
    assert resp.status_code == 202, resp.text
    facts = resp.json()["latest_version"]["parsed_facts"]
    assert facts["_extraction"]["status"] == "pending"
    # A fresh run was created.
    assert facts["_extraction"]["run_id"] != first_run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, facts["_extraction"]["run_id"])
        assert run is not None
        assert run.status == "queued"
        assert run.user_id == "u_reextract"


def test_reextract_404_for_other_user(client: TestClient) -> None:
    # Test 15: re-extract is user-scoped; cross-user returns 404.
    with _patch_get_queue_ok():
        upload = _upload(client, "resume.txt", _txt_bytes(), user="owner_re")
    assert upload.status_code == 201
    body = upload.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]

    with _patch_get_queue_ok():
        intruder = client.post(
            f"/api/v1/resumes/{resume_id}/versions/{version_id}/extract",
            headers={"X-User-Id": "intruder_re"},
        )
    assert intruder.status_code == 404


def test_upload_enqueue_failure_flips_run_to_failed(client: TestClient) -> None:
    # Test 16: when Redis is unavailable the upload endpoint flips the queued
    # AgentRun and extraction status to ``failed`` so the frontend never polls
    # forever.
    with patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(side_effect=OSError("redis down")),
    ):
        resp = _upload(client, "resume.txt", _txt_bytes(), user="u_redis_fail")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    extraction = body["latest_version"]["parsed_facts"]["_extraction"]
    assert extraction["status"] == "failed"
    run_id = extraction["run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "queue enqueue failed"


def test_upload_sanitizes_secret_from_queued_run_result(client: TestClient) -> None:
    # Test 17: the secret resume token must not leak into the queued AgentRun
    # result (which stores raw_text_len, not the text).
    secret_marker = "SUPER_SECRET_RESUME_TOKEN"
    content = f"name: {secret_marker}\nPython 5年".encode()
    with _patch_get_queue_ok():
        resp = _upload(client, "secret.txt", content, user="u_leak")
    assert resp.status_code == 201
    run_id = resp.json()["latest_version"]["parsed_facts"]["_extraction"]["run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        blob = json.dumps(run.result or {}, ensure_ascii=False) + (run.error or "")
        assert secret_marker not in blob
