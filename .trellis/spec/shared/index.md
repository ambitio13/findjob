# Shared Guidelines Index

These rules apply to every package and layer in the job-search agent system.

## Files

| File | Purpose | When to Read |
| --- | --- | --- |
| [code-quality.md](./code-quality.md) | Cross-language quality rules | Always |
| [dependencies.md](./dependencies.md) | Dependency and stack choices | Adding or changing dependencies |
| [typescript.md](./typescript.md) | TypeScript conventions for frontend and shared contracts | Frontend or shared type work |

## Mandatory Project Rules

- Keep business concepts named consistently across backend, frontend, database,
  logs, and tests.
- Validate input at boundaries; do not let raw platform, resume, or LLM output
  flow into durable storage without normalization.
- Preserve provenance for AI-generated content: source JD, resume version,
  prompt version, model/provider, and generation time.
- Make external side effects idempotent where possible, especially platform
  crawling, resume delivery, and HR messaging.
- Use structured logs and metrics for long-running agent work; plain print logs
  are not enough for production debugging.

