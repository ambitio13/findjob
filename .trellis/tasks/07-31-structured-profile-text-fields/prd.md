# Structured Profile Text Fields

## Goal

Replace vague profile constraints with explicit required and optional text-entry
fields that users can fill when resume parsing cannot infer them.

## Background

The current profile form exposes `constraints` as a JSON textarea. That is
developer-friendly but user-hostile. The desired direction is named fields with
clear labels. For this version, fields may all be plain text inputs/areas rather
than select controls.

## Requirements

- Remove or hide the generic JSON `constraints` textarea from the main profile
  editing experience.
- Define explicit required fields for the job-search agent's minimum useful
  profile, such as:
  - current target role/direction;
  - base city;
  - preferred cities;
  - expected salary range;
  - core strengths;
  - deal breakers / constraints;
  - work authorization or availability notes if needed later.
- Define optional fields as text-entry fields, such as:
  - preferred company types;
  - preferred industries;
  - remote/hybrid preference;
  - commute preference;
  - career goals;
  - notes for resume tailoring.
- Preserve compatibility with existing stored `constraints` data by mapping it
  into explicit fields where possible or showing it as legacy data outside the
  primary form.
- Keep backend validation typed and predictable; avoid arbitrary unvalidated JSON
  flowing from the UI.

## Acceptance Criteria

- [ ] The profile page no longer asks the user to edit raw JSON constraints.
- [ ] Required and optional fields are clearly separated.
- [ ] All fields in this version are editable as text inputs/textareas unless a
  field already has a stable existing typed representation.
- [ ] Existing user profile data remains readable after the change.
- [ ] JD analysis prompt context receives the updated explicit profile fields.

## Notes

- This should be implemented after resume auto profile parsing is designed, so
  parsed missing fields and form fields match.
