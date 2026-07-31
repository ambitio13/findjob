# Job Search Agent MVP Full Plan

## 1. Product Goal

Build a job-search assistant for job seekers. The MVP should help a user:

- define their career positioning and job-search preferences;
- upload a resume and turn it into durable resume facts;
- manually enter or import a JD;
- run a resume-aware Agent analysis for the JD;
- receive structured job analysis, match reasoning, risk points, skill gaps,
  interview-preparation suggestions, HR opening message, and resume rewrite
  snippets.

The MVP does not automate job-platform login, crawling, resume submission, HR
messaging, browser operation, or complete Word/PDF resume export.

## 2. Current State

The current implementation is a runnable project skeleton:

- FastAPI backend.
- React + Ant Design Pro frontend.
- Docker Compose with PostgreSQL and Redis.
- SQLAlchemy/Alembic initial models.
- Model Gateway abstraction with DeepSeek-compatible provider and fake provider.
- Lightweight Agent Runtime shape.
- Manual JD creation and job table/detail placeholder.

Important correction: the current Agent flow is still a smoke workflow. Even if
`MODEL_API_KEY` is configured, `manual_jd_analysis_demo` does not yet use the
model to produce real analysis.

## 3. Core Architecture

```text
Frontend: React + Ant Design Pro
  - profile/preferences form
  - resume upload and parsed facts view
  - manual JD entry
  - job table/detail
  - Agent run status
  - generated artifact display/edit

Backend: FastAPI
  - API routers
  - services/domain logic
  - repositories/database access
  - Model Gateway
  - Agent Runtime
  - Redis cache/coordination
  - PostgreSQL durable state
```

## 4. User Context Design

The product must be user-scoped before resume memory and Agent analysis are
meaningful.

MVP user model:

- Use a single current-user flow, not production auth.
- `get_current_user()` reads `X-User-Id` when present.
- If no header is present, use a fixed development user such as `demo_user`.
- If the user does not exist, create a default `UserProfile`.
- Every job, resume, resume version, AgentRun, generated artifact, and
  application record should be owned by `user_id`.

This lets us build multi-user-safe code now while deferring login/OAuth.

## 5. Memory Design

Resume data should be Agent long-term/permanent memory, but not as a raw file
dump.

### Permanent Fact Memory

Stored in PostgreSQL and versioned by `ResumeVersion`:

- work experience;
- project experience;
- skills;
- education;
- certificates;
- measurable achievements;
- facts that may be used in generated content;
- boundaries: facts the Agent must not invent.

### Long-Term User Profile

Stored in `UserProfile`:

- target role and direction;
- expected base/location;
- salary expectations;
- strengths;
- constraints;
- job-search strategy preferences.

### Short-Term Run Memory

Stored in `AgentRun`, `AgentStep`, `ToolCall`, and `GeneratedArtifact`:

- current JD;
- selected resume version;
- selected user profile;
- intermediate reasoning outputs;
- generated snippets and messages.

### Future Retrieval Memory

Vector/RAG storage can come later. First version should use PostgreSQL
structured facts; this is enough for one user profile, one selected resume
version, and one JD.

## 6. Resume-Aware Agent Design

The real Agent should not analyze a JD alone. JD-only analysis can explain the
role, but it cannot decide user fit.

Required input:

```json
{
  "job_id": "job_xxx",
  "resume_version_id": "resume_version_xxx",
  "user_id": "demo_user"
}
```

The Agent loads:

- job JD text and normalized job metadata;
- user profile and preferences;
- parsed facts from the selected resume version.

The Agent outputs:

- role summary;
- responsibilities;
- hard requirements;
- nice-to-have requirements;
- match score and evidence;
- risk score and risk reasons;
- salary/growth/stability notes;
- skill-gap plan;
- interview-preparation suggestions;
- short HR opening message;
- resume rewrite snippets.

Generated artifacts must store source IDs, prompt version, provider/model,
request ID, and timestamps.

## 7. Agent Runtime Layers

Use a lightweight PiAgent-style runtime:

- Planner: creates analysis steps from the workflow request.
- Executor: calls services, tools, and `ModelGateway`.
- Reflector: validates structured output and decides proceed/fail/retry.
- Tool Registry: declares allowed tools and schemas.
- Memory: loads profile, resume facts, JD context, and run state.
- Model Gateway: provider-neutral model interface.
- Persistence: saves `AgentRun`, `AgentStep`, `ToolCall`, and `Artifact`.

Do not call DeepSeek directly from business code. Only the Model Gateway
provider may perform provider-specific HTTP calls.

## 8. Execution Order

### Step 1: User Context + Profile

Task: `07-31-user-profile-preferences`

Build:

- `get_current_user()`;
- demo user fallback;
- `/api/v1/users/me` GET and update;
- profile/preference persistence;
- frontend profile form.

This must happen first so later data can be user-owned.

### Step 2: Resume Upload + Parsing Foundation

Task: `07-31-resume-upload-parsing-foundation`

Build:

- resume upload endpoint;
- file metadata storage;
- parser interface;
- parsed facts stored as `ResumeVersion`;
- frontend upload workflow;
- version listing.

This creates the Agent permanent fact memory.

### Step 3: Resume-Aware JD Analysis Agent

Task: `07-31-model-backed-jd-analysis-agent`

Rename/interpret as: Resume-Aware JD Analysis Agent.

Build:

- endpoint such as `POST /api/v1/jobs/{job_id}/analysis/run`;
- request includes selected `resume_version_id`;
- Agent loads user profile, resume facts, and JD;
- Agent calls DeepSeek through Model Gateway when API key exists;
- fake provider path remains deterministic for tests;
- `JobAnalysis` and `GeneratedArtifact` persistence;
- frontend job detail analysis view.

### Step 4: Generated Content Editing

Future task.

Build editable views for HR opening message, rewrite snippets, and skill-gap
plan. Still do not send messages or export complete resume files.

### Step 5: Dedicated Resume Export Tool

Future task.

Build a separate tool that assembles approved rewrite snippets into a complete
customized resume document. This is intentionally outside the current MVP
foundation.

## 9. Acceptance for True MVP

The MVP is not complete until:

- user profile/preferences can be saved;
- resume can be uploaded and parsed into a versioned fact set;
- manual JD can be created;
- Agent can analyze JD against selected resume/profile;
- model-backed flow works with DeepSeek through Model Gateway;
- fake provider tests remain deterministic;
- frontend displays the full analysis and generated artifacts.

