# Resume Upload and Parsing Foundation

## Goal

Implement resume upload and parsing foundation so later JD analysis can use
resume facts instead of generic placeholder context.

## Background

The skeleton contains resume and resume version database models plus a placeholder
router. There is no upload endpoint, no stored file metadata flow, no parser
adapter, no resume version API, and no frontend upload workflow.

This task should create the first reliable foundation. The parser may be a
stub/adapter if full PDF/DOCX extraction is not ready, but the contract must be
real and testable.

## Requirements

- Add upload endpoint for resume files with MIME/size validation.
- Store file metadata in `resumes` and parsed/versioned facts in
  `resume_versions`.
- Add parser interface and initial implementation:
  - plain text extraction when possible;
  - deterministic fallback/stub for unsupported formats;
  - no invented resume facts.
- Add APIs to list resumes and resume versions.
- Add frontend resume upload page or panel.
- Show uploaded resume metadata and latest parsed version.
- Keep uploaded file contents out of ordinary logs.
- Tests cover upload validation, version creation, and unsupported format
  behavior.

## Acceptance Criteria

- [ ] User can upload a resume from the frontend.
- [ ] Backend creates `Resume` and `ResumeVersion` records.
- [ ] Resume parser produces parsed facts or an explicit unsupported/stub result.
- [ ] `/api/v1/resumes` returns uploaded resumes.
- [ ] `/api/v1/resumes/{resume_id}/versions` returns versions.
- [ ] Sensitive resume contents are not logged.
- [ ] Tests cover successful upload, invalid type/size, and version listing.

## Out of Scope

- Pixel-perfect resume preview.
- Complete customized resume export.
- Production-grade OCR.
- Third-party document parsing SaaS integration unless explicitly chosen.

