# Logging and Observability

## Structured Logs

Use structured logs for backend and agent code. Include stable IDs and avoid
free-form string interpolation as the only source of context.

Useful fields:

- `request_id`
- `user_id`
- `agent_run_id`
- `tool_call_id`
- `job_id`
- `application_id`
- `platform`
- `status`
- `duration_ms`
- `error_code`

## Audit Logs

Create audit records for any external action:

- platform login or session refresh;
- job crawl or imported posting;
- resume submission;
- HR message send or reply;
- application status change.

Audit logs should be durable and queryable. They are not the same as debug logs.

## Redaction

Never log:

- passwords, cookies, access tokens, or platform session secrets;
- full resume text;
- private HR conversation content;
- full prompt text when it contains user-private data.

Log hashes, short IDs, counts, or redacted summaries instead.

### AgentStep result sanitization

`AgentStep.result` and `AgentRun.result` are persisted to PostgreSQL and may be
served via the agent-runs API. They must never contain raw resume text, full
prompt content, or any user-private data. Persist only sanitized metadata:
counts (e.g. `raw_text_len`), provider/model/prompt_version telemetry, latency,
and error categories. The same invariant applies to failure trails — a failed
step must record the failure category and error message, never the resume
content that was being processed.

## Metrics

Track at least:

- agent run count, success rate, cancellation rate, and failure category;
- tool-call latency and failure rate by platform;
- LLM token usage, latency, and provider errors;
- job discovery volume and analysis throughput;
- application submission attempts and duplicate-prevention hits.

