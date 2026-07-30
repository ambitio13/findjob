# Agent Framework Selection

## Current Status

The project will first build a lightweight internal PiAgent-style layered
runtime. No high-level agent framework is adopted for the MVP.

## Decision Criteria

Evaluate candidates by:

- typed tool-call schemas;
- durable run and step state;
- retry, timeout, cancellation, and human handoff support;
- observability for model calls and tool calls;
- RAG and memory integration;
- permission checks before external side effects;
- ease of testing agent plans without calling real platforms;
- deployment fit with FastAPI, Redis, PostgreSQL, and Docker.

## Decision for MVP

Use a small internal orchestration interface around planner, executor,
reflector, tools, memory, and audit logging. Keep it replaceable so a later
framework can be introduced without rewriting product services.

The model provider boundary is a model gateway using an OpenAI-compatible API
shape. The first concrete provider is DeepSeek. Business services and tools must
depend on the gateway interface, not on provider SDKs.

## Deferred

Revisit LangGraph or another durable workflow engine when the product adds
multi-platform automation, browser operation, long-running background workflows,
or approval pauses that need robust checkpoint/resume semantics.
