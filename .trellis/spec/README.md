# Job Search Agent Development Guidelines

These guidelines are the project-specific source of truth for future Trellis
implementation and review sessions.

Current evidence comes from:

- `项目立项/项目定位.md`
- `项目立项/业务.md`
- `项目立项/智能体工程化.md`
- `项目立项/面经也是项目中应该避免的.md`

There is no product source code in the repository yet, so these specs document
the agreed product direction and initial engineering conventions. Once backend
or frontend code exists, update the relevant spec with real file paths and
working examples from the codebase.

## Product Direction

Build a production-grade AI agent for job seekers. The system helps users:

- clarify career positioning and job-search expectations;
- upload and parse resumes;
- find and screen jobs across multiple platforms;
- analyze each JD for risk, salary, growth, stability, and fit;
- generate JD-specific resumes and short HR opening messages;
- record job opportunities and application state for later review;
- produce interview-preparation and skill-gap plans from the target JD.

The product must protect user autonomy. Any external action that changes state
outside this system, such as sending a message or submitting a resume, must be
explicitly represented, authorized, logged, and recoverable.

## Initial Technology Direction

- Frontend: React with Ant Design Pro.
- Backend: FastAPI.
- Data: PostgreSQL for durable business state.
- Cache and coordination: Redis.
- Runtime: Docker-based local and production packaging.
- Agent layer: framework choice is still open; choose by tool safety,
  observability, memory support, retry control, and production maintainability.

Do not introduce Next.js, oRPC, Drizzle, or Vercel AI SDK as defaults unless the
project later explicitly adopts them.

## Spec Structure

- [Shared](./shared/index.md): rules that apply across the whole repository.
- [Backend](./backend/index.md): FastAPI, data, agent, observability, and API
  contracts.
- [Frontend](./frontend/index.md): React, Ant Design Pro, tables, detail views,
  forms, and agent-operation UX.
- [Guides](./guides/index.md): cross-layer thinking and implementation checks.
- [Big Questions](./big-question/index.md): open architecture decisions that
  must be resolved with evidence before broad implementation.

## Global Rules

- Search existing specs and source before adding a new pattern.
- Keep domain language consistent: user, resume, job, JD, platform, application,
  HR message, insight, skill gap, task run, tool call, memory.
- Separate recommendation from execution. The agent may prepare an action, but
  user-visible approval gates must exist before irreversible external actions.
- Store every discovered job and generated artifact with enough provenance to
  explain why it was created.
- Treat resumes, job-search preferences, platform credentials, and HR messages
  as sensitive data.
- Prefer boring, inspectable architecture over hidden magic: typed contracts,
  persisted state transitions, structured logs, and repeatable tests.

