# Authentication and Authorization

## User Data

The system handles sensitive job-search data: resumes, salary expectations,
career preferences, platform credentials, HR messages, and interview plans.

Backend code must enforce ownership checks for every user-scoped resource.
Frontend visibility checks are useful for UX but do not replace backend checks.

## Permission Rules

- A user may only read and mutate their own profile, resumes, jobs,
  applications, generated artifacts, agent runs, and memory.
- Administrative capabilities must use separate roles and separate routes.
- Platform credentials and tokens must be encrypted or delegated to a secret
  manager before production use.
- Logs must never include raw credentials or full resume content.

## Human Approval Gates

The agent may prepare these actions, but must not execute them without explicit
user approval:

- submitting a resume;
- sending or replying to an HR message;
- logging in to or operating a third-party job platform on behalf of the user;
- changing a durable application status based on ambiguous external evidence.

Approval records should include user ID, target action, target platform, source
job/application IDs, approved payload version, timestamp, and execution result.

