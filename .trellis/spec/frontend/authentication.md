# Frontend Authentication

## Protected UI

Protect all app surfaces that display resumes, preferences, platform accounts,
job records, generated artifacts, or HR messages.

Frontend checks are for UX only. Backend authorization remains authoritative.

## Permission-Aware UX

- Hide or disable actions the user cannot perform.
- Explain when a platform account, credential, or approval is required.
- Ask for explicit confirmation before any external side effect.
- Show the exact job, platform, resume version, and message/rewrite artifact
  involved in an approval.

## Sensitive Data

- Avoid rendering full resumes or HR messages in generic logs, analytics, or
  error-reporting breadcrumbs.
- Do not store platform credentials in frontend state beyond the immediate form
  interaction.
