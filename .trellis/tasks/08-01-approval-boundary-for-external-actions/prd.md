# Approval Boundary for External Actions

## Goal

Create a durable approval boundary for future external actions. The system
should be able to represent "the user approved this exact planned action" and
block execution when approval is missing or stale.

This task still does not execute platform automation.

## Requirements

- Define planned external action preview shape.
- Store payload hash, source snapshot, action type, and approval metadata.
- Add approve/revoke/stale mechanics.
- Prevent future action execution unless approval exists and matches payload.
- Add UI surface showing exact payload preview before approval.
- Expire approval when job/resume/profile/artifact/payload changes.

## External Action Types To Prepare For

- platform submit;
- HR message;
- resume upload;
- profile/form fill;
- follow-up message.

## Failure Handling Requirements

- Missing approval blocks execution.
- Payload hash mismatch blocks execution and returns reapproval-required.
- Revoked approval blocks execution.
- Stale source snapshot invalidates approval.
- Approval write must be auditable in application timeline.

## Acceptance Criteria

- [ ] User can preview a planned action payload.
- [ ] User can approve that exact payload.
- [ ] Approval stores user id, timestamp, payload hash, source ids, and action
  type.
- [ ] Payload/source changes mark approval stale.
- [ ] Unapproved or stale actions are blocked by backend tests.
- [ ] No external action execution is implemented.

