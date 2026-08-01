# Design

## Approval Model

Start with a durable action object attached to ApplicationRecord. If migration
scope is acceptable, use a normalized `application_actions` table. If not,
store under timeline/readiness JSON but expose typed service functions.

Recommended shape:

```json
{
  "id": "action_id",
  "type": "platform_submit",
  "status": "draft | approval_required | approved | stale | revoked | blocked",
  "payload_preview": {},
  "payload_hash": "sha256:...",
  "source_snapshot": {},
  "approval": {
    "approved_by": "user_id",
    "approved_at": "timestamp",
    "approved_payload_hash": "sha256:..."
  },
  "stale_reason": null
}
```

## API Shape

```text
POST /applications/{id}/actions/preview
POST /applications/{id}/actions/{action_id}/approve
POST /applications/{id}/actions/{action_id}/revoke
GET  /applications/{id}/actions/{action_id}
```

## Payload Hash

Hash a normalized JSON representation of:

- action type;
- target platform/resource;
- selected artifacts;
- outgoing text;
- selected resume/file reference;
- source hash.

Do not hash raw secrets or browser/session data.

## Execution Guard

Future platform tools must call:

```python
assert_action_approved(action, current_payload_hash)
```

before doing anything external.

## Staleness

Approval becomes stale if:

- action payload changes;
- selected artifact changes;
- source hash changes;
- approval is revoked;
- approval expires by policy.

