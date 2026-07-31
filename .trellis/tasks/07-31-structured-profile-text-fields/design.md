# Structured Profile Text Fields — Design

## Context

The current profile form stores a compact `UserProfile` row:

- direct columns: `display_name`, `email`, `career_direction`, `base_location`,
  `preferred_locations`, `salary_min`, `salary_max`, `strengths`;
- flexible JSON: `constraints`.

The UI currently exposes `constraints` as a raw JSON textarea. That is useful
for developers but bad for users. The v1 correction replaces raw JSON editing
with named text fields while keeping the database change small and compatible
with resume fact extraction.

## Field Contract

### Required text-entry fields

These fields should be visible in the primary profile form and treated as the
minimum useful profile for the job-search agent:

| UI field | Storage | Source draft mapping |
| --- | --- | --- |
| Target role / direction | `career_direction` | `facts.target_direction` |
| Base city | `base_location` | first item of `facts.locations` |
| Preferred cities | `preferred_locations` as string list | `facts.locations` |
| Expected salary min / max | `salary_min`, `salary_max` numeric inputs | user-entered only for now |
| Core strengths | `strengths` as string list | `facts.strengths` + `facts.highlights` |
| Deal breakers / constraints | `constraints.deal_breakers` text | resume uncertain fields or user-entered |

### Optional text-entry fields

These are stored under named `constraints` keys, but the frontend must present
them as ordinary inputs/textareas, never as JSON:

| UI field | Storage |
| --- | --- |
| Preferred company types | `constraints.preferred_company_types` |
| Preferred industries | `constraints.preferred_industries` |
| Remote / hybrid preference | `constraints.work_mode_preference` |
| Commute preference | `constraints.commute_preference` |
| Career goals | `constraints.career_goals` |
| Resume tailoring notes | `constraints.resume_tailoring_notes` |
| Availability / work authorization notes | `constraints.availability_notes` |

Values can be strings or string arrays depending on the existing form component,
but API schemas must validate the shape before storing them in `constraints`.

## Profile Draft Apply Flow

The resume extraction task exposes draft fields in
`ResumeVersion.parsed_facts.facts`. This task owns the apply/merge action:

1. User opens resume detail and sees a draft preview.
2. User clicks "apply to profile".
3. Backend computes a preview diff:
   - `field`
   - `current_value`
   - `draft_value`
   - `will_change`
   - `blocked_reason` when a non-empty profile field would be overwritten.
4. If the user confirms, backend applies only allowed changes.
5. Non-empty existing profile fields are not overwritten unless
   `overwrite=true`.

The draft apply endpoint should be user-scoped and version-scoped:

`POST /api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft`

Payload:

```json
{
  "confirm": false,
  "overwrite": false
}
```

Default `confirm=false` returns preview only. `confirm=true` writes changes via
the same repository/service path used by `/users/me`.

## Backend Compatibility

No migration is required for v1. Existing `constraints` JSON remains readable.
The main rule is transport validation: the UI should not send arbitrary JSON;
schemas/services should map named fields into the known `constraints` keys.

Existing rows with legacy constraints that do not match the named keys should be
preserved and may be shown in a read-only "legacy notes" area outside the main
form.

## JD Analysis Compatibility

`jd_analysis_service._profile_to_dict` and the prompt should keep sending the
existing direct fields and the named constraints object. The model should see
clear labels rather than an opaque blob when feasible.

## Non-Goals

- A separate profile-drafts table.
- Authentication beyond the current `X-User-Id` / `get_current_user` demo
  boundary.
- Select-heavy UI. For this version, use text inputs/textareas except for
  stable numeric fields such as salary.
