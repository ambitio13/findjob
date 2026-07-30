# PostgreSQL JSONB Usage

## Use Normal Columns For

- user ID and ownership;
- platform;
- job title, company, location/base, salary range;
- application status;
- risk score and match score;
- resume version ID;
- generated artifact type;
- created and updated timestamps.

These fields are filtered, sorted, joined, or indexed by product workflows.

## Use JSONB For

- original third-party platform payload snapshots;
- LLM provider metadata;
- prompt variables snapshot;
- tool-call raw result after redaction;
- analysis explanation details that are not frequently filtered.

## Rules

- JSONB fields must have a documented schema shape at the service boundary.
- Promote a JSONB field to a normal column once it becomes a common filter,
  join, or sort key.
- Do not store secrets or unredacted private conversations in JSONB just because
  it is convenient.

