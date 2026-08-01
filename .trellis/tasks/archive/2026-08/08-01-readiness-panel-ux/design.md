# Design

## Placement

Prefer application detail as the canonical surface. Job detail can show a
compact readiness entry point and link/create the application record.

## Component Shape

```text
ApplicationDetailPage
  ReadinessSummary
  SourceSnapshotPanel
  ArtifactChecklist
  FailurePanel
  TimelinePanel
  ApprovalPreviewSlot (disabled until approval task)
```

## Display States

- Empty: no application record yet.
- Planned: record exists, no selected resume/materials.
- Preparing: active generation run exists.
- Ready: materials exist and current.
- Stale: materials exist but source changed.
- Failed: latest operation failed.
- Approval required: materials ready but external action is not approved.
- Submitted/follow-up: application is now tracking outcome.

## Failure Panel

Render failure envelope fields:

- category/code;
- safe message;
- retryable tag;
- next action button;
- agent run link;
- source metadata summary.

## Polling

Reuse existing async AgentRun polling patterns. Poll the application detail
resource after terminal runs to hydrate artifacts/timeline, not only the run.

## Accessibility / Copy

- Buttons should describe actions, not internal implementation.
- "AI 建议" should be labeled as suggestion.
- "重新生成" should warn when source changed.
- "标记已投递" is manual and should not imply platform verification.

