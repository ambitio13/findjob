# Agent, LLM, RAG, and Memory Guidelines

The file name is kept for compatibility with existing Trellis manifests. It is
not a requirement to use Vercel AI SDK.

## Agent Architecture

Represent production agent work as explicit components:

- entry layer: authentication, rate limit, session routing;
- planner: creates steps and acceptance checks;
- executor: calls tools and services;
- reflector: validates result, detects failure, and decides bounded re-plan;
- tool layer: platform adapters, resume parsing, retrieval, generation;
- memory layer: short-term context, user profile, business knowledge, vector
  retrieval, and durable business records;
- observability layer: logs, metrics, audit events, traces.

## Tool Calling

Tool calls must not rely on prompt instructions alone.

- Define an input and output schema for every tool.
- Validate and normalize model-provided arguments.
- Enforce permissions before execution.
- Use idempotency keys for state-changing calls.
- Record tool result, latency, retry count, and failure category.
- Add bounded retries and a clear fallback path.

## RAG and Memory

Use different stores for different memory needs:

- conversation summary: short-term context;
- user profile and preferences: PostgreSQL;
- resumes and generated artifacts: versioned PostgreSQL records;
- business knowledge and reusable interview material: retrieval store;
- hot tool results and session cache: Redis.

Chunking strategy should follow content type. Resume sections, JD sections,
interview questions, and platform rules should be chunked by semantic unit, not
by arbitrary token count alone.

## Prompt and Output Rules

- Prompts should separate facts from instructions.
- Never ask the model to invent resume facts.
- Store prompt version and source IDs with generated output.
- Validate structured outputs before saving.
- Display AI analysis as analysis, not verified truth.

