# Evolution Contracts (P0–P6)

Contracts and invariants introduced by the P0–P6 evolution plan. Every future
change touching these areas must preserve them; the golden-set regression and
the test suite are the executable guardians, but code review must check intent,
not just green tests.

---

## 1. Approval Boundary (highest priority)

- The agent may only *prepare* external side effects (send HR message, submit
  resume, operate a third-party platform). Execution always requires explicit
  user approval through the existing approval flow.
- Follow-up suggestions (P5) are advisory rows only. Creating, dismissing, or
  actioning a suggestion must never enqueue, trigger, or shortcut any external
  action. Acting on a suggestion must route through the normal approval path.
- `resolve_suggestion` semantics: `404` = does not exist or not owned by the
  caller, `409` = already resolved (actioned/dismissed). No other status codes
  for these cases.

## 2. Match Safety Gate

- `apply_match_safety_gate` may only **downgrade** decisions. A non-`communicate`
  input must never become `communicate` (covered by golden-set invariant test).
- Downgrade triggers: score below `min_score`, missing (`None`) / empty /
  whitespace-only opening message for communicate (a communicate decision
  without a usable message has nothing lawful to send), PII patterns (ID card,
  phone, email, ...) in the opening message.
- `min_score` is parameterized; the caller injects the user's calibrated
  threshold (`followup_service.active_match_threshold`). Default is
  `COMMUNICATE_MIN_SCORE` (0.6); calibration clamps to `[0.4, 0.8]`.
- Golden gate cases assert the gated `opening_message` **content** (expected
  text or stripped input), not just message presence.

## 3. Authentication & Isolation

- Resolution order in `deps.get_current_user`: Bearer JWT (subject validated
  against `auth_users`) → non-prod fallback (`X-User-Id` / demo user) → `401`
  in prod. The fallback must never be reachable in prod.
- Every user-scoped read/write enforces ownership server-side. Frontend checks
  are UX only.
- Rate limiting (Redis token bucket) is middleware-level; the userscript bridge
  additionally requires a channel token (shared secret + time window).

## 4. Privacy Invariants

- The userscript session scan is **read-only** and reports only sanitized state
  transitions (`userscript_observed` source). Chat content is never uploaded.
- `job_postings.jd_raw` is encrypted at rest via the `EncryptedText` column
  type (`app/db/types.py`): ORM writes encrypt, ORM reads decrypt, raw SQL sees
  only the `enc1$<fernet-token>` envelope. Key derivation and envelope
  semantics live in `app.core.content_crypto` (Fernet key = SHA-256 of
  `AUTH_SECRET_KEY`; prod **must** configure it, non-prod falls back to a fixed
  dev key). Legacy plaintext rows pass through `decrypt_text` unchanged until
  migration `0010_encrypt_jd_raw` rewraps them. New code must never write
  `jd_raw` outside the ORM (raw-SQL inserts would store plaintext).
- `/userscript-bridge/probe` is a diagnostic-only surface: disabled entirely in
  prod (`403 probe_disabled_in_prod`) and restricted to a diagnostic op
  whitelist (`count`, `check_visible`, `read_title`, `read_url`, `read_jd`,
  `probe_elements`) elsewhere. `read_content` is explicitly forbidden through
  probe. Adding or widening an op that returns page/DOM text requires a privacy
  review.
- Logs must not contain resume content, credentials, or tokens.

## 5. Outcome & Metrics (P1)

- `ApplicationOutcome.occurred_at` is mandatory; `source ∈ {manual,
  userscript_observed}`; evidence is a summary, never raw chat.
- Funnel metrics bucket by opening-message `prompt_version` and by match score.
  Any new outcome producer must keep both dimensions populated so the funnel
  remains answerable ("which opening message wins").

## 6. Traceability (P2)

- Every rewrite in a `targeted_resume` artifact must trace back to a resume
  fact. Traceability failure = refuse generation, never fall back to invented
  content. There is no frontend path that writes edited resume text back in a
  way that bypasses this check.

## 7. Calibration (P5)

- Purely statistical: p25 (linear interpolation) of `match_score` over the
  replied group, divided by 100, clamped to `[0.4, 0.8]`. Fewer than
  `CALIBRATION_MIN_SAMPLES` (5) samples or an empty replied group keeps the
  default. No model training, no online learning.
- `JobAnalysis.match_score` is on a **0–100** scale. Do not compare it against
  0–1 thresholds without dividing.
- `ThresholdCalibration` is append-only; the latest row wins.

## 8. Suggestion Rules (P5)

Deterministic and auditable, no LLM in the loop:

- A: submitted ≥ `NO_REPLY_DAYS` (3) days with no reply-type outcome →
  `change_opening_message`.
- B: reply-type outcome present and no pending `skill_gap_plan` → create one,
  embedding the latest `jd_analysis` artifact's `skill_gaps`.
- C: `OutcomeReflector` flags a low-reply-rate direction
  (`min_samples=5`, `low_reply_rate=0.10`) → one suggestion per active-status
  application in that direction.
- Dedup: an existing pending suggestion of the same type+application skips.
- `scan_all_users` isolates failures per user (try/except + rollback).

## 9. Scheduling

- The daily scan is an arq cron job (`daily_followup_scan`, 09:00) and is also
  registered as a plain function so it can be enqueued manually. The handler
  opens its own session; it must not share request-scoped sessions.

## 10. Evaluation (P6)

- Golden sets live in `backend/app/tests/golden/` and run offline against pure
  functions (no network, no model). Behavior changes that alter gated decisions
  must fail `test_prompt_regression.py` until the golden annotations are
  deliberately updated.
- Red-flag annotations must use the `RedFlagType` vocabulary (guarded via
  `get_args`). Corpus size guards: gate cases ≥ 10, red-flag cases ≥ 4.

## 11. Migration Hygiene

- Single alembic head. The P1/P4 double branch was merged by `0008_merge_heads`
  (a no-op merge; keep it import-free). `0010_encrypt_jd_raw` is a data
  migration that re-encrypts legacy plaintext `jd_raw` rows in batches and
  imports `app.core.content_crypto` so runtime and migration share one crypto
  implementation. New migrations chain from `0010`.
- New tables must be added to the TRUNCATE cleanup list in
  `backend/app/tests/conftest.py`.
- Static route segments (e.g. `/applications/follow-up-suggestions`) must be
  registered before parameterized segments (`/{application_id}`).

## Forbidden Patterns

- Trusting `X-User-Id` in prod.
- Upgrading a decision inside the safety gate.
- Persisting full JD text or chat content **in plaintext** (or bypassing the
  `EncryptedText` column with raw-SQL writes).
- Enabling `/userscript-bridge/probe` in prod or widening its op whitelist
  without a privacy review.
- Letting suggestions or calibration execute external actions.
- Generating resume content that cannot be traced to a resume fact.
- Introducing a second alembic head without a merge migration.
