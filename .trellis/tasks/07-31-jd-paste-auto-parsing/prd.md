# JD Paste Auto Parsing

## Goal

Let users paste raw JD text and automatically parse structured job fields,
instead of manually filling a multi-field job form first.

## Background

Manual JD entry is acceptable for the first internal version, but the desired UX
is paste-first: copy a JD from a platform, let the system parse company/title/
location/salary/direction/requirements, then let the user correct fields before
saving.

## Requirements

- Provide a paste-first JD input flow.
- Parse raw JD into structured fields:
  - title;
  - company;
  - platform/source when inferable or manually selectable;
  - location;
  - salary range;
  - direction;
  - responsibilities;
  - hard requirements;
  - nice-to-have requirements;
  - benefits or risk clues when present.
- Let users review and edit parsed fields before creating/updating the job.
- Preserve raw JD text as the source of truth.
- Model calls, if used, must go through `ModelGateway`.
- Parsing failures must be visible and recoverable; user should still be able to
  save raw JD manually.

## Acceptance Criteria

- [ ] User can paste raw JD and receive structured draft fields.
- [ ] User can edit parsed fields before saving.
- [ ] Saved jobs retain raw JD and structured metadata.
- [ ] Parse failures do not block manual save.
- [ ] Tests cover successful parse, partial parse, failure fallback, and user
  ownership.

## Notes

- This is P2 behind observability and resume/profile corrections.
