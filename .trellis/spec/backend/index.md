# Backend Guidelines Index

Backend code is expected to use FastAPI with PostgreSQL, Redis, Docker, and a
production-ready agent layer.

## Files

| File | Purpose | When to Read |
| --- | --- | --- |
| [directory-structure.md](./directory-structure.md) | Backend module layout | Starting backend work |
| [api-contracts.md](./api-contracts.md) | HTTP API and schema contracts | Adding or changing endpoints |
| [type-safety.md](./type-safety.md) | Python typing and Pydantic rules | Defining schemas or services |
| [database.md](./database.md) | PostgreSQL data ownership and query rules | Persistence work |
| [authentication.md](./authentication.md) | Auth, permissions, and user approval gates | User or platform actions |
| [error-handling.md](./error-handling.md) | API, agent, and tool failure handling | Any failure-prone workflow |
| [logging.md](./logging.md) | Structured logs, audit logs, and tracing | Observability work |
| [performance.md](./performance.md) | Async work, caching, concurrency, and queues | Long-running or high-volume work |
| [ai-sdk-integration.md](./ai-sdk-integration.md) | LLM, agent, tool calling, RAG, and memory | AI features |
| [quality.md](./quality.md) | Backend verification checklist | Before reporting backend work done |

## Core Backend Rules

- FastAPI route handlers should validate transport concerns, then delegate to
  domain services.
- Domain services own business decisions such as job scoring, JD analysis,
  resume tailoring, and application-state transitions.
- Agent orchestration must be represented as durable runs and steps, not hidden
  inside one untracked model call.
- Tool adapters must validate schemas, enforce permissions, use idempotency
  keys, and return structured results.
- PostgreSQL is the durable source of truth. Redis is for cache, locks, queues,
  rate limits, and temporary session context.

