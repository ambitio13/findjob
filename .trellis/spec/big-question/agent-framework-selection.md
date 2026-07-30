# Agent Framework Selection

## Current Status

The initiation notes say the project should think carefully about agent
engineering and choose an agent framework later. No framework is adopted yet.

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

## Default Until Decided

Use a small internal orchestration interface around planner, executor,
reflector, tools, memory, and audit logging. Keep it replaceable so a later
framework can be introduced without rewriting product services.

