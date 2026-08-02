# API Contracts

## FastAPI Boundary

FastAPI routes are transport boundaries. They should:

- declare typed request and response schemas;
- use dependency injection for auth, database sessions, and request context;
- call service-layer functions for business behavior;
- return stable error shapes;
- avoid direct LLM calls, browser automation, or complex SQL in route handlers.

## Resource Shape

Use nouns that match the product domain:

- `/users/me`
- `/resumes`
- `/resumes/{resume_id}/versions`
- `/jobs`
- `/jobs/{job_id}/analysis`
- `/applications`
- `/applications/{application_id}/artifacts`
- `/agent-runs`
- `/agent-runs/{run_id}/approve`

## Contract Rules

- Keep response objects stable for table and detail views.
- Include pagination for list endpoints from the start.
- Include status fields for asynchronous work.
- Use request IDs and operation IDs for side-effecting calls.
- Return generated artifacts by ID and metadata; avoid stuffing large generated
  documents into every list response.

## Async Workflow Shape

For long-running work, return an agent run or task ID:

```json
{
  "agent_run_id": "run_123",
  "status": "queued"
}
```

The frontend should poll, subscribe, or refresh by run ID rather than waiting
for one blocking request.

## Bridge Endpoint Pattern

Some clients cannot carry authentication headers (e.g. Tampermonkey userscripts
running inside a third-party page). For these, use unauthenticated bridge
endpoints with these rules:

- **No auth dependency.** The userscript cannot send `X-User-Id`. Security is
  enforced at the instruction layer: instructions carry only operations and
  selectors (no credentials); results carry only sanitized values.
- **Long-poll for instruction retrieval.** `GET /next-instruction` blocks up to
  5 seconds, then returns `204 No Content` if no instruction is queued. The
  userscript polls again immediately.
- **POST for result and heartbeat.** `POST /result` posts back the result of an
  instruction; `POST /heartbeat` keeps the connection alive.
- **In-memory only.** The instruction queue is a process-local `asyncio.Queue`.
  It is never persisted to Redis or PostgreSQL. If the process restarts, the
  queue is lost — this is acceptable because the human re-initiates.
- **Config-gated.** Bridge endpoints are registered only when
  `boss_userscript_bridge_enabled=True`. The endpoint base URL and any session
  token are process configuration, never in request payload or DB.
- **Connection status.** `GET /status` returns whether a userscript is currently
  connected (heartbeat within 15 seconds) and the active application ID. The
  frontend uses this to show a bridge status indicator.

## Userscript Read Capability Exception

Bridge endpoints default to diagnostic-sized sanitized values. If a product flow
requires reading user-visible third-party page content, define an explicit read
operation instead of overloading `read_content`.

### 1. Scope / Trigger

- Trigger: BOSS recommended-job flow needs to read the current JD from the
  user's active browser page.
- This is allowed only for user-initiated platform workflows, not background
  scraping.

### 2. Signatures

Bridge instruction:

```json
{
  "instruction_id": "uuid",
  "op": "read_jd",
  "page_id": "string",
  "expected_url_hash": "sha256:xxxxxxxx",
  "max_text_chars": 8000,
  "selector_profile": "boss_recommended_job_v1"
}
```

Bridge result:

```json
{
  "instruction_id": "uuid",
  "page_id": "string",
  "success": true,
  "jd": {
    "title": "string",
    "company": "string | null",
    "location": "string | null",
    "salary": "string | null",
    "experience": "string | null",
    "education": "string | null",
    "skills": ["string"],
    "description": "string",
    "source_kind": "boss_recommended_job",
    "page_url_hash": "sha256:xxxxxxxx"
  },
  "error": null
}
```

### 3. Contracts

- `read_jd` returns text fields, never raw HTML.
- `description` is length-limited and cleaned on both userscript and backend.
- The backend must reject results missing `page_id`, mismatching URL hash, or
  insufficient JD fields.
- Validated JD may become normal product data with provenance. The bridge queue
  itself remains in-memory and non-durable.

### 4. Validation & Error Matrix

- Missing/mismatched `page_id` -> reject result.
- Mismatched `page_url_hash` -> reject result.
- HTML/script-like payload -> reject or strip, then fail if unsafe content
  remains.
- JD below minimum required fields -> `jd_too_sparse`.
- Result exceeds max length -> truncate before validation or reject according to
  service contract.

### 5. Good/Base/Bad Cases

- Good: returns title, company, salary, location, and description for the active
  BOSS job page.
- Base: returns title and description but optional fields are absent; backend
  may proceed with lower confidence.
- Bad: returns `document.body.innerHTML` or a complete page dump. This must be
  rejected.

### 6. Tests Required

- Schema tests for accepted and rejected `read_jd` payloads.
- Sanitizer tests for script/HTML/token stripping.
- API tests proving mismatched page/session data is rejected.
- Service tests proving sparse JD stops before communication.

### 7. Wrong vs Correct

#### Wrong

```json
{"op": "read_content", "text": "<html>...</html>"}
```

#### Correct

```json
{"op": "read_jd", "jd": {"title": "...", "description": "clean text"}}
```
