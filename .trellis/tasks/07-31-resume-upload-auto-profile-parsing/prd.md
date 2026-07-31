# Resume Upload Auto Profile Parsing

## Goal

Automatically parse uploaded resumes into durable resume facts and user profile
draft fields, so the resume becomes useful long-term agent memory immediately
after upload.

## Background

The current upload flow extracts raw text for `.txt`, `.pdf`, and `.docx`, but
`parsed_facts` mostly stores parser telemetry such as `_parser` and
`_parser_status`. That is not enough for resume-aware JD analysis or profile
construction.

## Requirements

- Uploading a parseable resume must trigger structured extraction after text
  extraction.
- The extraction should produce normalized candidate facts such as:
  - name / contact facts when available;
  - education;
  - work experiences;
  - projects;
  - skills;
  - years of experience;
  - target direction clues;
  - locations;
  - strengths and highlights;
  - missing / uncertain fields that need user confirmation.
- The workflow must write structured facts to durable storage associated with
  the `ResumeVersion`.
- The workflow must create or expose a profile draft/update path, not silently
  overwrite user-entered profile fields without review.
- Unsupported or sparse resumes must still produce a clear parser/extraction
  status and next action.
- Model calls, if used, must go through `ModelGateway`; no direct provider calls.

## Acceptance Criteria

- [ ] Uploading a supported resume creates raw text and structured resume facts.
- [ ] The frontend shows that profile extraction happened, failed, or needs
  confirmation.
- [ ] User-entered profile fields are not overwritten without an explicit merge
  or confirmation path.
- [ ] JD analysis can consume structured resume facts in addition to raw text.
- [ ] Tests cover supported upload, sparse extraction, unsupported format, and
  user ownership.

## Notes

- This task depends on P0 agent observability patterns, because extraction should
  eventually be auditable too.
