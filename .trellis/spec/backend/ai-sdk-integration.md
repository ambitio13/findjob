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
- model gateway: OpenAI-compatible provider abstraction, initially backed by
  DeepSeek;
- observability layer: logs, metrics, audit events, traces.

## Framework Decision

First implementation should use a lightweight internal PiAgent-style layered
runtime instead of a high-level agent framework. The runtime should model:

- `AgentRun`: one user-requested workflow;
- `AgentStep`: one planned step inside a run;
- `ToolCall`: one validated tool execution or model-mediated tool request;
- `Artifact`: one saved generated output, such as JD analysis, HR opening
  message, resume rewrite snippet, or skill-gap plan.

LangGraph or another durable workflow runtime can be introduced later when the
project starts platform automation or long-running human-in-the-loop flows. The
internal runtime must keep planner, executor, reflector, tools, memory, and
model gateway replaceable.

## Model Gateway

All model access must go through a backend model gateway. Do not call DeepSeek,
OpenAI, or any provider SDK directly from route handlers, services, frontend
code, or individual tools.

The first provider should use an OpenAI-compatible client shape with DeepSeek:

```python
class ModelGateway:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        ...

    async def structured(self, request: StructuredRequest[T]) -> T:
        ...
```

Provider configuration should live in backend settings:

- `MODEL_PROVIDER=deepseek`
- `MODEL_BASE_URL=https://api.deepseek.com`
- `MODEL_API_KEY`
- `MODEL_DEFAULT_MODEL`

The gateway response must include provider, model, latency, token usage when
available, and a stable request ID for logs and persisted artifacts.

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

## MVP Output Scope

The first product version does not generate a complete Word/PDF resume file.
It generates:

- JD analysis;
- resume optimization suggestions;
- JD-specific project, skills, and experience rewrite snippets;
- short HR opening message;
- skill-gap and interview-preparation plan.

A later dedicated resume-export tool may assemble a full customized resume from
approved snippets and templates. Keep current artifact types granular so that
future exporter can reuse them.
