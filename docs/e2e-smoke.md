# E2E Smoke Test Guide

This document explains how to run the BOSS userscript-bridge E2E smoke test,
how to interpret failures, and how to clean up residual data.

## Overview

The smoke test (`scripts/e2e-smoke.sh`) verifies the full BOSS integration
stack end-to-end against a running Docker Compose environment:

1. **Compose services** — all containers are running.
2. **Service health** — backend `/health` (DB + Redis ok), frontend `/`, worker
   startup log evidence.
3. **Bridge protocol** — heartbeat → status → wrong-tab 204 → probe
   bounded-failure → result success/failure → queue drain.
4. **Inspect bounded-failure** — `POST /boss/recommended-jobs/current/inspect`
   returns `inspect_status=read_failed` within a bounded time when no real
   userscript can read a JD.
5. **Cleanup** — Redis keys under the smoke namespace are deleted; DB residue
   (UserProfile + AgentRun) is reported with manual cleanup instructions.

## Prerequisites

- Docker and Docker Compose installed and running.
- Ports 8000 (backend), 5173 (frontend), 5432 (postgres), 6379 (redis)
  available.
- `python3` available on PATH (used for JSON parsing — no `jq` dependency).
- `curl` available.

## Running the Smoke Test

### Full pass (starts Compose stack)

```bash
./scripts/e2e-smoke.sh
```

This runs `docker compose up -d --build` with an isolated `QUEUE_NAMESPACE`,
then executes all 5 phases. The stack is left running after the test.

### Skip Compose up (stack already running)

```bash
./scripts/e2e-smoke.sh --skip-up
```

Use this when the Compose stack is already running and you don't want to
rebuild.

### Custom configuration

```bash
BACKEND_BASE_URL=http://localhost:8000/api/v1 \
FRONTEND_BASE_URL=http://localhost:5173 \
SMOKE_QUEUE_NAMESPACE=my-smoke-ns \
SMOKE_USER_ID=my-smoke-user \
./scripts/e2e-smoke.sh --skip-up
```

### Help

```bash
./scripts/e2e-smoke.sh --help
```

## Test Isolation

The smoke test uses timestamp-based isolation to prevent cross-environment
contamination:

| Variable               | Default                              | Purpose                          |
|------------------------|--------------------------------------|----------------------------------|
| `SMOKE_QUEUE_NAMESPACE`| `job-search-agent-smoke-<timestamp>` | Isolates arq queue keys in Redis |
| `SMOKE_USER_ID`        | `smoke-review-user-<timestamp>`      | Isolates DB rows (UserProfile, AgentRun) |
| `SMOKE_PAGE_ID`        | `smoke-page-<timestamp>`             | Isolates bridge page binding     |

The production namespace `job-search-agent` is **never** used. All isolation
values are printed at the start of each run.

When run with `docker compose up` (no `--skip-up`), the `QUEUE_NAMESPACE`
environment variable is set for the entire Compose stack, so both backend and
worker use the isolated namespace. This prevents a running default-namespace
worker from consuming smoke jobs.

## Interpreting Failures

### Phase 1: Docker Compose Services

| Failure                              | Likely Cause                        | Fix                                  |
|--------------------------------------|-------------------------------------|--------------------------------------|
| `docker compose up` failed           | Build error or port conflict        | Check build output; free ports       |
| Services not running within 60s      | Container crash or slow startup     | `docker compose logs <service>`      |

### Phase 2: Service Health

| Failure                              | Likely Cause                        | Fix                                  |
|--------------------------------------|-------------------------------------|--------------------------------------|
| Backend /health did not respond      | Backend not started or crashed      | `docker compose logs backend`        |
| Backend DB not "ok"                  | Postgres not ready or wrong DB URL  | `docker compose logs postgres`       |
| Backend Redis not "ok"               | Redis not ready                     | `docker compose logs redis`          |
| Frontend / did not respond           | Nginx not started                   | `docker compose logs frontend`       |
| Worker not running                   | Worker crashed on startup           | `docker compose logs worker`         |

### Phase 3: Bridge Protocol

| Failure                              | Likely Cause                        | Fix                                  |
|--------------------------------------|-------------------------------------|--------------------------------------|
| Heartbeat failed                     | Backend not responding              | Check backend health first           |
| Bridge not connected                 | Heartbeat didn't register           | Retry; check backend logs            |
| Wrong-tab → 200 (not 204)            | Stale instruction in queue          | Restart backend container            |
| Probe returned success=true          | Unexpected — real userscript?       | Check for stray userscript sessions  |
| Correct-tab → 200 (not 204)          | Stale instruction not drained       | Restart backend container            |

### Phase 4: Inspect Bounded-Failure

| Failure                              | Likely Cause                        | Fix                                  |
|--------------------------------------|-------------------------------------|--------------------------------------|
| Inspect timed out (>120s)            | Unbounded failure — backend bug     | Check backend logs; inspect endpoint |
| Inspect status not "read_failed"     | Bridge not connected or JD read OK  | Check bridge status; expected failure|
| Job/Application not null             | Unexpected success path             | Check if real userscript is connected|

### Phase 5: Cleanup

Cleanup failures are non-fatal. They indicate that Redis keys or DB rows could
not be automatically removed. Manual cleanup instructions are printed.

### Worker Log Red Flags

After a smoke run, check worker logs for cross-namespace pollution:

```bash
docker compose logs --tail=200 worker
```

Look for:
- `missing_run` warnings — indicates the worker consumed a job without a
  corresponding AgentRun (cross-namespace pollution).
- `traceback` — unhandled exceptions in the worker.
- `smoke` namespace in job IDs — should NOT appear if isolation is working.

## Cleanup

### Automatic (during smoke run)

- Redis keys matching `SMOKE_QUEUE_NAMESPACE:*` are deleted.
- The Compose stack is left running (use `docker compose down` to stop).

### Manual (DB residue)

The inspect endpoint creates a `UserProfile` and `AgentRun` for
`SMOKE_USER_ID`. These are **not** automatically deleted because the smoke
test uses the business database (not an isolated DB). To clean up:

```bash
# Delete AgentRuns for smoke users
docker compose exec postgres psql -U app -d job_search_agent \
  -c "DELETE FROM agent_runs WHERE user_id IN (
    SELECT id FROM user_profiles WHERE user_id LIKE 'smoke-review-user-%'
  );"

# Delete smoke UserProfiles
docker compose exec postgres psql -U app -d job_search_agent \
  -c "DELETE FROM user_profiles WHERE user_id LIKE 'smoke-review-user-%';"
```

### Full reset

To stop and remove all Compose resources (including volumes):

```bash
docker compose down -v
```

> **Warning**: `docker compose down -v` deletes the postgres data volume.
> Use only in development.

## Known Behavior: Stale Instructions in Queue

The `/probe` endpoint sends an instruction via `put_instruction`, which
enqueues it then blocks in `_take_result`. On timeout (90s with no real
userscript), `_take_result` returns a timeout result but does **not** dequeue
the instruction. The stale instruction lingers in the channel queue.

The smoke test handles this by draining stale instructions before the final
queue-empty check (Phase 3, step 3g). This is expected behavior, not a bug —
the `/probe` endpoint is a temporary diagnostic tool (P0-2 verification) and
will be removed after selector confirmation.

## CI Integration

The smoke test can be integrated into CI by running it after a successful
build:

```bash
docker compose up -d --build
./scripts/e2e-smoke.sh --skip-up
exit_code=$?
docker compose down -v
exit $exit_code
```

The script exits with code 0 on success, 1 on any failure.
