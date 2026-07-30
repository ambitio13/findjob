# Table and Detail Views

The initiation notes require users to view discovered jobs like records in a
table, with details available per job. Build this as a first-class workflow.

## Job Table

Recommended columns:

- platform;
- company;
- job title;
- city/base;
- salary range;
- direction or role family;
- match score;
- risk level;
- application status;
- updated time;
- actions.

Filters should support platform, status, location, salary, direction, risk
level, and keyword search.

## Detail View

Each job detail should show:

- original JD and platform metadata;
- normalized role information;
- analysis summary, risks, salary interpretation, growth/stability notes;
- matched resume facts;
- generated HR opening message;
- generated or tailored resume artifact;
- application timeline and agent run history.

## Interaction Rules

- Keep analysis, generated artifacts, and external execution visually separate.
- Users must be able to inspect the generated message/resume before approval.
- Preserve record IDs in routes or query state so refresh and sharing are stable.

