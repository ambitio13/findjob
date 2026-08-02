# Future Plan — BOSS Recommended Jobs Contact Loop

## Status

Deferred. Do not implement this before the current single-application BOSS
guided-submit pilot is complete and manually validated.

The current task should first prove:

- real BOSS page navigation works;
- dry-run fill stops before final submit/contact;
- login/CAPTCHA/selector/rate-limit failures are safely classified;
- approval + idempotency guards cannot be bypassed;
- results are written to timeline/failure envelopes.

Only after that should the product expand to a recommendation-list loop.

## User Goal

From BOSS recommended jobs:

```text
open recommended jobs
  -> inspect next unseen job
  -> capture JD
  -> judge whether JD matches the user's profile/resume
  -> if not matched, record skip and continue
  -> if matched, click "立即沟通"
  -> generate/open HR opening message
  -> send or stop for approval, depending on policy
  -> record result
  -> continue to next job
```

This is a larger loop than the current task. It combines discovery, JD
extraction, matching, external contact, opener generation, result tracking, and
iteration.

## Why This Is Deferred

The loop has more external side effects than a single guided submit:

- opening many job details may trigger rate limits;
- clicking "立即沟通" may itself create a platform-side contact;
- sending an opener is an irreversible external message;
- duplicate detection must work across platform job IDs, not just one
  application action;
- selector drift can happen in both list pages and detail/contact modals.

Building it before the single-action pilot is stable would make failures harder
to diagnose. First close the small loop; then scale it.

## Required Domain Additions

### Recommendation Cursor

Track list iteration state so the agent can resume without re-processing the
same cards:

- platform: `boss`;
- list source: recommended jobs URL/filter identity;
- cursor/page/scroll position;
- last seen platform job IDs;
- stopped reason: completed, rate_limited, login_required, captcha_required,
  user_paused, error.

### Platform Job Deduplication

Each BOSS job external ID should map to at most one durable `JobPosting` per
user. If the same job appears again:

- reuse the existing job record;
- do not re-run expensive analysis unless the source changed;
- do not create duplicate application/contact actions.

### Discovery Run

Use a new AgentRun workflow, for example:

```text
boss_recommended_jobs_contact_loop
```

Suggested steps:

1. `open_recommendation_list`
2. `load_cursor`
3. `inspect_job_card`
4. `open_job_detail`
5. `extract_jd`
6. `upsert_job_posting`
7. `run_jd_parse`
8. `run_match_analysis`
9. `decide_match`
10. `prepare_contact_action`
11. `approval_or_policy_guard`
12. `click_contact`
13. `fill_or_send_opener`
14. `record_result`
15. `advance_cursor`

## Matching Policy

The first version should use explicit thresholds, not fuzzy agent judgment
alone.

Recommended default:

- match score >= 75;
- risk score <= 40;
- no hard constraint violation from user profile;
- JD is fresh and parseable;
- resume version is selected and current.

Every skip should be recorded with a stable reason:

- low_match_score;
- high_risk_score;
- missing_salary/location constraint;
- stale_jd;
- unparseable_jd;
- duplicate_job;
- already_contacted;
- user_paused.

## Contact Policy

Important product decision for the future task:

- Safe first version: automatically discover, parse, match, and generate the
  opener, then stop before sending for user approval.
- Later version: allow policy-based auto-send only after enough pilot evidence
  exists and the user explicitly enables it.

Recommended first loop:

```text
auto discovery + auto JD matching + auto opener draft
  -> approval_required before "立即沟通"/send
```

If product scope later chooses "matched jobs can auto-contact", that policy
must be represented as a durable user setting with:

- maximum contacts per run/day;
- match/risk thresholds;
- platform rate-limit handling;
- idempotency key;
- emergency stop/pause;
- full audit timeline.

## External Action Mapping

Treat `立即沟通` and opener sending as external actions:

- action_type: `hr_message` or a new explicit `platform_contact`;
- target_platform: `boss`;
- target_resource: platform job ID or contact URL;
- payload_hash: exact opener + job ID + resume/profile source hash;
- idempotency key:

```text
{application_id}:{action_type}:{payload_hash}
```

or, if no application exists yet:

```text
{user_id}:{platform}:{platform_job_id}:{action_type}:{payload_hash}
```

Do not click/contact/send without passing approval or an explicit auto-contact
policy guard.

## Failure Matrix Additions

The future loop must handle all current platform failures plus list-loop
failures:

| Failure | Required behavior |
| --- | --- |
| Recommendation list unavailable | stop, keep cursor, manual review |
| Job card selector drift | stop or skip with diagnostic reference |
| JD extraction incomplete | mark job unparseable, continue if safe |
| Duplicate job/contact | mark duplicate, advance cursor |
| Match analysis failed | retry bounded once, then skip/manual review |
| Login expired | stop and ask user to log in |
| CAPTCHA | pause; user handles challenge |
| Rate limit | stop; preserve cursor; retry later |
| Contact button disabled/missing | record platform_contact_unavailable |
| Opener generation failed | keep job, no contact, retry/manual |
| Unknown contact result | mark unknown, stop or ask user |

## Acceptance Criteria For Future Task

- Agent can process a bounded number of recommended jobs, e.g. max 5 per pilot
  run.
- Every visited platform job ID is recorded as seen/skipped/contacted/unknown.
- JD extraction creates or updates `JobPosting` without storing credentials or
  page HTML.
- Matching decision is deterministic enough to test.
- Non-matching jobs are skipped with reasons and no external contact.
- Matching jobs create a contact action with exact opener payload hash.
- Sending/contact is impossible without approval or explicit auto-contact
  policy.
- Duplicate jobs/contact attempts are prevented by idempotency keys.
- Cursor resumes after pause/rate-limit/login-expired.
- No autonomous unlimited loop exists.

## Dependency On Current Task

Do not start this future loop until:

- `RealBossAdapter.prepare_submission` is real, not a safe unknown stub;
- stop-before-submit/contact behavior is manually proven;
- platform classification helpers exist and are tested;
- approval/idempotency submit path remains green;
- one manual pilot run has produced usable evidence in `check.jsonl`.
