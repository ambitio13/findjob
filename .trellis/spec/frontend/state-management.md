# State Management

## State Ownership

- Backend state: users, resumes, jobs, applications, agent runs, generated
  artifacts, approvals, and audit data.
- URL state: table filters, selected tab, pagination, sort, and selected record
  when shareable.
- Form state: unsaved edits to profile, search preferences, HR messages, and
  generated resume content.
- Local component state: open/closed panels, temporary selection, and visual
  controls.

## Rules

- Do not store durable workflow state only in React state.
- Do not treat AI streaming text as final output until the backend confirms the
  generated artifact was saved.
- Keep approval modals backed by a specific action ID and artifact version.
- Cache server reads through the selected data-fetching library once the
  frontend stack is initialized.

## Status Machines

Represent user-facing status with finite values. Avoid ad hoc display text as
state.

