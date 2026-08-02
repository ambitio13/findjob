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

## Bridge Channel Security

The userscript bridge channel (used by Tampermonkey scripts running inside
third-party pages) has no user authentication header. Security is enforced at
the instruction and result layer instead:

- **Instructions are operation-only.** An instruction carries an operation type
  (`fill`, `click`, `check_visible`, `count`, `read_title`, `read_url`,
  `read_content`), a selector (kind + value + name), and an optional fill value.
  It never carries credentials, cookies, tokens, or profile paths.
- **Instructions are backend-constructed.** The userscript never generates
  selectors or decides what to do. It only executes what the backend sends.
- **Results are sanitized.** Results carry only: `visible` (bool), `count`
  (int), `text` (truncated title), `url` (sha256 hash only), and `error`
  (diagnostic-stripped). Raw HTML, raw JD, and raw resume never cross the
  channel.
- **The queue is pure memory.** The `asyncio.Queue` is process-local and never
  persisted to Redis or PostgreSQL. `clear()` is called after each operation.
- **No navigation instructions.** The bridge never instructs the userscript to
  navigate (`page.goto`). The human navigates manually; the backend only
  verifies the current page state.
- **The submit click is gated.** The `click` instruction is only allowed during
  the submit phase (`is_submit_phase=True`). During prepare, `click` raises
  `RuntimeError`. During submit, `click_count` is asserted `<= 1`.
- **Process config only.** The bridge endpoint base URL and any session token are
  environment configuration, never in request payload, queue, or database.

