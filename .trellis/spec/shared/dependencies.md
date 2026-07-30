# Dependency Guidelines

## Baseline Stack

Use the stack from the project initiation documents unless a later decision
record changes it:

- FastAPI for backend HTTP APIs.
- React with Ant Design Pro for frontend application UI.
- PostgreSQL for durable data.
- Redis for caching, rate limits, distributed coordination, and hot session
  data.
- Docker for reproducible local and deployable runtime environments.

## Dependency Selection Rules

- Prefer mature libraries with typed interfaces, active maintenance, and clear
  production behavior.
- Do not add a large framework just to wrap one endpoint or one prompt.
- For agent frameworks, evaluate tool-call control, state persistence,
  observability, retry/timeout behavior, and human approval gates before
  adoption.
- For job-platform integration, prefer official APIs when available. Browser
  automation is acceptable only behind rate limits, platform-specific adapters,
  and explicit user authorization.
- Add one dependency at the layer that owns the behavior. For example, crawling
  helpers belong in backend integration code, not frontend components.

## Version and Runtime Rules

- Pin backend and frontend dependencies once package manifests exist.
- Keep Docker images reproducible; avoid installation steps that depend on
  hidden local machine state.
- Document service dependencies and required environment variables in the same
  change that introduces them.
- When choosing between Redis, PostgreSQL, and vector storage, record what
  consistency and retrieval behavior the feature requires.

