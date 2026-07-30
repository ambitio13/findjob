# Frontend Type Safety

## Rules

- Do not use `any` in new frontend code.
- Import backend-generated types once contract generation exists.
- Keep status values as unions or enums, not display strings.
- Treat optional API fields carefully; missing salary, location, or risk score
  must not crash table rendering.
- Validate user-edited generated artifacts before sending them back to the
  backend.

## Domain Types

Frontend code should use the same domain vocabulary as the backend:

- `ResumeVersion`
- `JobPosting`
- `JobAnalysis`
- `ApplicationRecord`
- `GeneratedArtifact`
- `HrMessageDraft`
- `AgentRun`
- `ToolCall`
- `ApprovalRequest`

## Display Mapping

Map backend values to display labels in one place per domain. Do not scatter
label/color mappings across table columns, detail panels, and modals.

