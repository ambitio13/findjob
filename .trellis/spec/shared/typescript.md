# TypeScript Guidelines

## Scope

Use TypeScript for React, Ant Design Pro screens, frontend hooks, API clients,
and any shared generated contracts.

## Rules

- Do not use `any` in new code. If external data is unknown, validate it and
  narrow it into a domain type.
- Keep domain type names aligned with backend schemas: `UserProfile`, `Resume`,
  `JobPosting`, `JobAnalysis`, `ApplicationRecord`, `HrMessage`,
  `SkillGapPlan`, `AgentRun`, and `ToolCall`.
- Import generated API types when the backend contract tooling exists. Do not
  hand-copy backend response shapes into frontend modules.
- Distinguish nullable, optional, and empty values. An unknown salary range is
  not the same as a salary range of zero.
- Represent state machines as unions rather than free-form strings.

```ts
type ApplicationStatus =
  | "discovered"
  | "analyzed"
  | "approved"
  | "submitted"
  | "replied"
  | "rejected"
  | "interviewing";
```

## Frontend API Shape

Frontend code should treat backend responses as authoritative. Transform them
for display at the boundary of a page, table, or detail component; do not mutate
contract objects in place.

