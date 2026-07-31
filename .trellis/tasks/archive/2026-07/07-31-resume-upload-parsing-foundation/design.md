# Resume Upload and Parsing Foundation — Design

## 1. Context & Scope

This task builds the resume upload + parsing foundation on top of the
current-user ownership boundary established in `07-31-user-profile-preferences`.
The goal is a real, testable contract: upload a file, persist metadata +
parsed text, list resumes and their versions. Later JD analysis (#3) can then
read resume facts instead of placeholder context.

### In scope

- `POST /api/v1/resumes` (multipart upload) with MIME/size validation
- Local-disk file storage + `storage_uri` in DB
- Parser adapter: real text extraction for `.txt` / `.pdf` / `.docx`; explicit
  `unsupported` status for accepted-but-unparsed resume formats (`.doc`,
  `.rtf`); **no invented resume facts**
- `GET /api/v1/resumes` (list, user-scoped)
- `GET /api/v1/resumes/{resume_id}` (detail, includes latest version raw_text)
- `GET /api/v1/resumes/{resume_id}/versions` (version list, no raw_text)
- Frontend `/resumes` page: Upload dragger + list table + detail drawer
- Tests: upload success, invalid type, invalid size, version listing, user
  isolation, parser output

### Out of scope (do NOT implement)

- Structured fact extraction (named entities, skills graph) — `parsed_facts`
  stores parser metadata only; only `raw_text` is real resume content
- Re-parse / new-version-of-existing-resume endpoint (each upload = new Resume
  with version 1; the versioning *machinery* exists but no re-upload endpoint)
- Pixel-perfect resume preview / rendering
- OCR for scanned PDFs
- Production-grade file storage (S3 / object store) — local disk only
- Complete customized resume export
- Third-party parsing SaaS

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| File storage | Local disk + DB metadata (`storage_uri`) | MVP-simple; docker volume for durability; swap path later |
| Parser depth | Real text extraction for txt/pdf/docx; parser metadata only in `parsed_facts` | Honest contract; no invented facts; structured extraction deferred to #3+ |
| Versioning | Each upload creates Resume + ResumeVersion(version_no=1) | Simplest correct foundation; version table ready for future re-parse |
| User scoping | `Resume.user_id = current_user.id`; cross-user → 404 | Reuses `get_current_user`; matches `authentication.md` ownership rule |
| Size limit | 10 MB (configurable) | Covers typical resume size; prevents abuse |
| File type validation | Accept resume-like extensions (`.txt`, `.pdf`, `.docx`, `.doc`, `.rtf`); parse first three; mark `.doc` / `.rtf` as unsupported | Keeps upload validation real while still testing the parser's deterministic unsupported path. Client MIME is logged but not trusted. |
| raw_text in responses | Detail only; excluded from list & version-list | Keeps list payloads small; raw_text can be long |
| Logging | Never log raw_text or file bytes; log lengths + IDs only | `logging.md` redaction rule |
| Migration | None — existing `resumes` / `resume_versions` schema suffices | Models already have all columns |

## 3. Files Touched

```
backend/
  app/
    core/config.py              # + resume_upload_dir, resume_max_size_mb
    services/
      __init__.py
      resume_parser.py          # NEW: parse_resume(content, mime, filename) -> ParseResult
      resume_storage.py         # NEW: save_upload(user_id, resume_id, filename, content) -> storage_uri
    db/repositories/
      resume_repo.py            # NEW: create, get, list_for_user, create_version, list_versions, latest_version
    schemas/
      resume.py                 # NEW: ResumeOut, ResumeDetailOut, ResumeVersionOut, ResumeVersionListItem
    api/v1/
      resumes.py                # REPLACE placeholder: POST upload, GET list, GET detail, GET versions
    tests/
      conftest.py               # + RESUME_UPLOAD_DIR temp dir for tests
      test_resume_upload.py     # NEW
  pyproject.toml                # + pdfplumber, python-docx
  Dockerfile                    # (no change; deps install via pyproject)
docker-compose.yml              # + resume_data volume + mount to backend
frontend/
  src/
    types/index.ts              # + Resume, ResumeDetail, ResumeVersion types
    api/client.ts               # + uploadResume, listResumes, getResume, listResumeVersions
    features/resumes/
      ResumeUpload.tsx          # NEW: Upload.Dragger
      ResumesTable.tsx          # NEW: list + detail drawer
    pages/resumes/ResumesPage.tsx  # NEW
    components/layout/AppLayout.tsx  # + "简历" menu item
    main.tsx                    # + /resumes route
```

## 4. Backend Design

### 4.1 Config additions (`core/config.py`)

```python
# --- Resume upload (MVP local storage) ---
resume_upload_dir: str = "/data/resumes"
resume_max_size_mb: int = 10
```

- `resume_upload_dir` points at the docker volume mount in prod; local dev
  overrides via env to `./data/resumes`.
- `resume_max_size_mb` enforced before reading the full body.

### 4.2 Parser (`services/resume_parser.py`)

```python
from dataclasses import dataclass
from typing import Any, Literal

@dataclass
class ParseResult:
    raw_text: str
    parsed_facts: dict[str, Any]   # parser metadata only — no invented facts
    status: Literal["parsed", "unsupported"]
    parser_name: str               # "text" | "pdfplumber" | "python-docx" | "unsupported"

def parse_resume(content: bytes, mime_type: str, filename: str) -> ParseResult: ...
```

- `.txt` → decode utf-8 (fallback latin-1), `parser_name="text"`
- `.pdf` → `pdfplumber.open(BytesIO(content))`, concat page text,
  `parser_name="pdfplumber"`
- `.docx` → `python-docx`, concat paragraph text, `parser_name="python-docx"`
- `.doc` / `.rtf` → `status="unsupported"`, `raw_text=""`,
  `parser_name="unsupported"`
- other extensions are rejected by the upload endpoint before parser dispatch
- `parsed_facts` contains parser telemetry only, e.g.
  `{"_parser": "pdfplumber", "_parser_status": "parsed"}`. It must not contain
  inferred skills, companies, dates, or other resume facts in this task.
  Structured extraction is deferred; the column exists so later tasks can
  populate real facts without a migration.

### 4.3 Storage (`services/resume_storage.py`)

```python
def save_upload(
    upload_dir: str, user_id: str, resume_id: str,
    filename: str, content: bytes,
) -> str:
    """Persist file to {upload_dir}/{user_id}/{resume_id}/{safe_filename}.

    Returns the storage_uri (relative path from upload_dir). Filename is
    sanitized to basename to prevent path traversal.
    """
```

- Path structure: `{upload_dir}/{user_id}/{resume_id}/{safe_filename}`
- `resume_id` is a fresh UUID hex, guaranteeing no collision
- `filename` stored in DB is basename-only display metadata.
- `safe_filename` used on disk applies a conservative character allowlist to
  the basename stem, preserves a safe lowercase extension, and falls back to
  `resume.<ext>` / `resume` when the stem has no safe characters.
- Same uploaded filenames do not collide because every upload writes under a
  fresh `resume_id` directory.
- Creates parent dirs

### 4.4 Repository (`db/repositories/resume_repo.py`)

Functions:
- `create(db, user_id, filename, storage_uri, mime_type) -> Resume`
- `get(db, resume_id) -> Resume | None`
- `list_for_user(db, user_id, page, page_size) -> tuple[list[Resume], total]`
- `create_version(db, resume_id, raw_text, parsed_facts) -> ResumeVersion`
  - `version_no` = max existing + 1 (1 for first)
- `list_versions(db, resume_id) -> list[ResumeVersion]`
- `latest_version(db, resume_id) -> ResumeVersion | None`

### 4.5 Schemas (`schemas/resume.py`)

```python
class ResumeVersionOut(BaseSchema):
    id: str
    version_no: int
    parsed_facts: dict[str, Any] | None = None
    raw_text: str | None = None
    created_at: datetime | None = None

class ResumeVersionListItem(BaseModel):
    """Version summary without raw_text (keeps list payloads small)."""
    id: str
    version_no: int
    created_at: datetime | None = None
    parser_status: str | None = None     # parsed_facts["_parser_status"]
    parser_name: str | None = None       # parsed_facts["_parser"]

class ResumeOut(BaseSchema):
    id: str
    filename: str
    mime_type: str | None = None
    created_at: datetime | None = None
    latest_version_no: int | None = None

class ResumeDetailOut(BaseSchema):
    id: str
    filename: str
    mime_type: str | None = None
    storage_uri: str | None = None
    created_at: datetime | None = None
    latest_version: ResumeVersionOut | None = None
```

> Note on parser metadata: `parsed_facts` stores lightweight parser telemetry
> (`_parser`, `_parser_status`) only. This is not invented resume content; it is
> needed to distinguish parsed vs unsupported versions without adding a
> migration. `ResumeVersionListItem` reads those two keys.

### 4.6 Endpoints (`api/v1/resumes.py`)

```
POST   /api/v1/resumes                      multipart, file
GET    /api/v1/resumes                      ?page=&page_size=
GET    /api/v1/resumes/{resume_id}
GET    /api/v1/resumes/{resume_id}/versions
```

**POST /resumes** (upload):
- Depends on `get_current_user`
- `file: UploadFile = File(...)`
- Validate extension against allowlist `.{txt,pdf,docx,doc,rtf}`; reject other
  extensions.
- Validate size while reading chunks; stop and return 413 once accumulated
  bytes exceed `resume_max_size_mb`. FastAPI has already accepted the multipart
  request by then, but the app must not persist or parse oversized content.
- Save to disk via `resume_storage.save_upload`
- `resume_repo.create(...)` → `resume_repo.create_version(...)` (parse result)
- Log: `user_id`, `resume_id`, `filename`, `mime_type`, `size_bytes`,
  `parser_name`, `raw_text_len` — **never** raw_text or bytes
- Return `ResumeDetailOut` (201)

**GET /resumes**:
- User-scoped list, paginated, `ResumeOut` items (no raw_text)

**GET /resumes/{resume_id}**:
- Fetch resume; 404 if missing OR `user_id != current_user.id`
- Return `ResumeDetailOut` with latest version (includes raw_text)

**GET /resumes/{resume_id}/versions**:
- Same ownership 404 check
- Return list of `ResumeVersionListItem` (no raw_text)

### 4.7 Error handling

| Case | Status | Detail |
|---|---|---|
| Rejected extension | 422 | `unsupported file type: .xxx` |
| File too large | 413 | `file exceeds {max} MB limit` |
| Empty filename | 422 | `filename is required` |
| Accepted but unparsed resume format (`.doc`, `.rtf`) | 201 | `latest_version.parsed_facts["_parser_status"] == "unsupported"` |
| Resume not found / cross-user | 404 | `resume not found` |
| Parser raises (corrupt PDF) | 422 | `failed to parse file: {error}` (no raw content) |

### 4.8 User scoping & ownership

All endpoints depend on `get_current_user`. `Resume.user_id` is set to
`current_user.id` on upload. List filters by `user_id`. Detail/versions return
404 for missing OR cross-user (not 403, to avoid revealing existence) — same
pattern as jobs.

## 5. Database & Migration

- **No schema change.** Existing `Resume` and `ResumeVersion` models already
  have: `user_id` (not null, indexed), `filename`, `storage_uri`, `mime_type`,
  `version_no`, `parsed_facts` (JSON), `raw_text` (Text), timestamps.
- **No new migration file.**
- Parser status is stored inside `parsed_facts` (`_parser`, `_parser_status`)
  as metadata, avoiding a schema change. Do not store inferred resume facts in
  this task.

## 6. Frontend Design

- `ResumesPage` at `/resumes`: two-column layout (upload + table) like JobsPage.
- `ResumeUpload`: Ant Design `Upload.Dragger`, `beforeUpload` validates
  extension + size client-side, uploads via `uploadResume()` (FormData).
- `ResumesTable`: `ProTable` listing resumes; click row → drawer showing
  detail (metadata + raw_text in a scrollable `<Typography.Paragraph>`).
- Menu: add "简历" (`/resumes`) to `AppLayout`, after "职位".
- Error UX: reuse `apiErrorMessage`.

## 7. Testing Plan

Backend (`tests/test_resume_upload.py`):
1. Upload `.txt` → 201, Resume + ResumeVersion created, raw_text persisted.
2. Upload `.pdf` → 201, raw_text non-empty (use a minimal generated PDF or
   small fixture).
3. Upload `.docx` → 201, raw_text non-empty (generate with `python-docx` in
   the test to avoid committing binary fixtures).
4. Upload accepted-but-unparsed `.doc` or `.rtf` → 201, raw_text empty,
   `_parser_status == "unsupported"`, no invented facts.
5. Upload rejected `.html` → 422.
6. Upload oversized (> limit) → 413 and does not create DB rows or files.
7. `GET /resumes` only lists current user's resumes.
8. `GET /resumes/{id}` returns 404 for another user's resume.
9. `GET /resumes/{id}/versions` returns version list without raw_text.
10. Re-upload is a new Resume (version_no=1 each).
11. raw_text never appears in logs (assert via captured log output).

Frontend: existing `pnpm lint/type-check/build` stays green.

## 8. Dependencies

Add to `backend/pyproject.toml`:
```
"pdfplumber>=0.11",
"python-docx>=1.1",
```

Both are pure-Python-friendly and work in the slim Docker image.

## 9. Tradeoffs & Risks

- **Local disk is not durable across container rebuilds** unless a named
  volume is mounted. We add `resume_data` volume in docker-compose.
- **pdfplumber pulls in pdfminer.six** — adds ~5MB to the image. Acceptable
  for real parsing.
- **Parser metadata is not resume facts**: `parsed_facts` may contain
  `_parser` / `_parser_status` only. Filling it with inferred skills,
  experience, or education would violate the PRD ("no invented resume facts").
- **Client MIME spoofing**: we trust extension over content_type for MVP. A
  future hardening could sniff magic bytes.
- **No file cleanup on resume delete**: there is no DELETE endpoint in scope,
  so orphaned files are not a concern yet.
