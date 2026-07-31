# Resume Upload and Parsing Foundation — Implementation Plan

Phases A→H. Each phase has a concrete deliverable and validation step.
Verify after each phase before moving to the next.

## Phase A — Config & dependencies

- [ ] A1. `backend/pyproject.toml`: add `"pdfplumber>=0.11"`,
      `"python-docx>=1.1"` to dependencies. Add `app.services` is already in
      packages (verify). Run `pip install -e ".[dev]"` (or equivalent) to
      install in the venv.
- [ ] A2. `backend/app/core/config.py`: add
      `resume_upload_dir: str = "/data/resumes"` and
      `resume_max_size_mb: int = 10`.
- [ ] A3. `docker-compose.yml`: add `resume_data` named volume; mount to
      backend service at `/data/resumes`; pass `RESUME_UPLOAD_DIR` env.

**Verify**: config loads; `pdfplumber` / `docx` import without error.

## Phase B — Parser & storage services

- [ ] B1. `backend/app/services/resume_parser.py` (NEW):
      `ParseResult` dataclass + `parse_resume(content, mime_type, filename)`.
      Handle txt/pdf/docx; return `unsupported` for accepted-but-unparsed
      resume formats (`.doc`, `.rtf`). `parsed_facts` must contain parser
      telemetry only (`_parser`, `_parser_status`), never inferred resume facts.
- [ ] B2. `backend/app/services/resume_storage.py` (NEW):
      `save_upload(upload_dir, user_id, resume_id, filename, content) -> str`.
      Sanitize filename to basename; build path
      `{dir}/{user_id}/{resume_id}/{safe}`; apply a conservative filename
      character allowlist; mkdir parents; write bytes; return relative
      storage_uri.

**Verify**: unit-call `parse_resume` on a sample txt/pdf/docx; confirm output.

## Phase C — Repository & schemas

- [ ] C1. `backend/app/db/repositories/resume_repo.py` (NEW):
      `create`, `get`, `list_for_user`, `create_version` (version_no = max+1),
      `list_versions`, `latest_version`.
- [ ] C2. `backend/app/schemas/resume.py` (NEW): `ResumeOut`,
      `ResumeDetailOut`, `ResumeVersionOut`, `ResumeVersionListItem`.

**Verify**: import compiles; types align with ORM model.

## Phase D — Router (replace placeholder)

- [ ] D1. `backend/app/api/v1/resumes.py` (REPLACE):
      - `POST /resumes` upload: validate ext + size, save, create resume +
        version, return `ResumeDetailOut` (201).
      - Accepted extensions are `.txt`, `.pdf`, `.docx`, `.doc`, `.rtf`; parse
        the first three and persist an explicit unsupported version for `.doc`
        / `.rtf`. Reject non-resume extensions such as `.html` with 422.
      - Size validation reads chunks and returns 413 before persistence/parsing
        once accumulated bytes exceed the configured limit.
      - `GET /resumes`: user-scoped paginated list.
      - `GET /resumes/{resume_id}`: detail with latest version raw_text.
      - `GET /resumes/{resume_id}/versions`: version list (no raw_text).
      - Ownership 404 pattern.
      - Logging: IDs + lengths only.

**Verify**: `app.openapi()` shows the 4 routes; smoke `POST` with a txt file
via httpx/TestClient.

## Phase E — Tests

- [ ] E1. `backend/app/tests/test_resume_upload.py` (NEW): 11 tests per
      design §7. Use small inline fixtures (txt bytes; minimal pdf/docx
      generated at test time; avoid committing binary fixtures unless needed).
- [ ] E2. `backend/app/tests/conftest.py`: ensure `RESUME_UPLOAD_DIR` points
      to a tmp path for tests (override via env in conftest before app import).

**Verify**: `DATABASE_URL=...test .venv/bin/pytest -q` all green.

## Phase F — Backend validation gate

- [ ] F1. `ruff check app` + `ruff format --check app` clean.
- [ ] F2. `pytest -q` all green (existing + new).
- [ ] F3. Scope grep: no `auto_submit`, `selenium`, `playwright`, `完整简历导出`.
      Do not grep for plain `ocr`: it appears in out-of-scope documentation and
      dependency docs; instead review changed application files for production
      OCR implementation.

## Phase G — Frontend

- [ ] G1. `frontend/src/types/index.ts`: add `Resume`, `ResumeDetail`,
      `ResumeVersion`, `ResumeVersionListItem`.
- [ ] G2. `frontend/src/api/client.ts`: add `uploadResume(file)`,
      `listResumes(page, pageSize)`, `getResume(id)`,
      `listResumeVersions(resumeId)`.
- [ ] G3. `frontend/src/features/resumes/ResumeUpload.tsx` (NEW):
      `Upload.Dragger` with client-side ext/size validation; calls
      `uploadResume`.
- [ ] G4. `frontend/src/features/resumes/ResumesTable.tsx` (NEW):
      `ProTable` + row click → drawer with detail (raw_text scrollable).
- [ ] G5. `frontend/src/pages/resumes/ResumesPage.tsx` (NEW): two-column
      layout (upload + table).
- [ ] G6. `frontend/src/components/layout/AppLayout.tsx`: add "简历" menu
      item → `/resumes`.
- [ ] G7. `frontend/src/main.tsx`: add `/resumes` route.

## Phase H — Frontend validation gate

- [ ] H1. `pnpm lint` clean.
- [ ] H2. `pnpm type-check` clean.
- [ ] H3. `pnpm build` succeeds.

## Validation Commands Summary

```bash
# Backend
cd backend
.venv/bin/ruff check app
.venv/bin/ruff format --check app
DATABASE_URL="postgresql+psycopg://app:app@localhost:5432/job_search_agent_test" .venv/bin/pytest -q

# Frontend
cd frontend
pnpm lint
pnpm type-check
pnpm build

# Scope guard
grep -rniE 'auto_submit|selenium|playwright|完整简历导出' backend/app frontend/src || echo "CLEAN"
```

## Out-of-Scope Reminders (do NOT implement)

- No structured resume fact extraction (NER, skills graph). `parsed_facts`
  stores parser metadata only.
- No re-parse / new-version-of-existing endpoint. Each upload = new Resume v1.
- No DELETE endpoint.
- No production OCR, no SaaS parsing integration.
- No pixel-perfect resume preview / rendering.
- No complete customized resume export.

## Done Definition

- All Phase A–H checkboxes ticked.
- `design.md` decisions honored (local disk + DB metadata, real txt/pdf/docx
  parsing, parser metadata only in `parsed_facts`, user-scoped ownership 404,
  no raw_text in logs or list responses).
- Validation commands all green.
- Ready for Phase 3.3 (spec update review) → 3.4 (commit) → `/trellis:finish-work`.
