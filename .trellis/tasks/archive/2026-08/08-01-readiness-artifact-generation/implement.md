# Implementation Plan

## Step 1 — Prompt and Schema Contracts

- Add prompt templates for four artifact types.
- Add Pydantic output schemas.
- Add fake gateway branches or deterministic test gateways.

## Step 2 — Queue Payload and Worker

- Add typed payload for artifact generation.
- Add worker handler.
- Reuse model gateway and AgentRun step conventions.

## Step 3 — API Submit Endpoint

- Add generate endpoint under applications.
- Enforce duplicate active run policy.
- Return 202 with run summary.

## Step 4 — Persistence

- Write `GeneratedArtifact` only after validation.
- Append application timeline event.
- Store source hash/provenance.

## Step 5 — Tests

- Success for each artifact type.
- Queue failure.
- Model failure.
- Invalid JSON/schema failure.
- Duplicate active run.
- Stale source handling.
- Sanitization: no raw resume/JD in run/step metadata.

## Validation

```bash
cd backend && pytest app/tests/test_readiness_artifacts.py
cd backend && ruff check .
```

