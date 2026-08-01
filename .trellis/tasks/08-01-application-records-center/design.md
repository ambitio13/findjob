# Design

## Backend Boundary

Routes validate transport and user ownership, then delegate to service/repo:

```text
api/v1/applications.py
  -> services/application_service.py
  -> db/repositories/application_repo.py
  -> db/models/ApplicationRecord
```

## API Shape

```text
GET    /api/v1/applications
POST   /api/v1/applications
GET    /api/v1/applications/{application_id}
PATCH  /api/v1/applications/{application_id}/status
POST   /api/v1/applications/{application_id}/timeline
```

## Create Payload

```json
{
  "job_id": "job_id",
  "resume_version_id": "version_id|null"
}
```

## Status Payload

```json
{
  "status": "materials_ready",
  "note": "optional user-safe note",
  "failure": null
}
```

## Timeline Event Shape

Keep MVP timeline in JSON, but write it through a helper:

```json
{
  "id": "event_id",
  "type": "created | status_changed | failure | user_note | artifact_linked",
  "at": "timestamp",
  "actor": "user | system | agent",
  "from_status": "planned",
  "to_status": "preparing",
  "summary": "safe text",
  "metadata": {}
}
```

## Data Model Notes

Current `ApplicationRecord` has `job_id`, `resume_version_id`, `status`,
`timeline`. This task can extend it with minimal columns:

- `user_id` for direct scoping and indexing;
- `latest_agent_run_id`;
- `latest_error`;
- `readiness_snapshot`.

If migration risk needs to stay low, some fields can be JSON first, but user
scoping should be explicit before platform automation.

## Transaction Rule

Status update and timeline append happen in one transaction. A timeline that
claims a transition happened while the row status did not change is invalid.

