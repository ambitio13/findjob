# Frontend Guidelines Index

Frontend code is expected to use React with Ant Design Pro for an operational
job-search agent application.

## Files

| File | Purpose | When to Read |
| --- | --- | --- |
| [directory-structure.md](./directory-structure.md) | React module layout | Starting frontend work |
| [components.md](./components.md) | Component and Ant Design Pro rules | Building UI |
| [table-detail-views.md](./table-detail-views.md) | Job/application list and detail patterns | Tables, records, dashboards |
| [hooks.md](./hooks.md) | Custom hook and data-fetching conventions | Creating hooks |
| [state-management.md](./state-management.md) | URL, server, form, and local state | State decisions |
| [api-integration.md](./api-integration.md) | Backend API usage and async run handling | Calling backend APIs |
| [authentication.md](./authentication.md) | Auth UI and permission-aware flows | Protected views |
| [ai-sdk-integration.md](./ai-sdk-integration.md) | Agent UI, streaming, approvals, and generated artifacts | AI-facing UI |
| [css-layout.md](./css-layout.md) | Layout, density, responsiveness | UI layout |
| [type-safety.md](./type-safety.md) | TypeScript frontend rules | Type work |
| [quality.md](./quality.md) | Frontend verification checklist | Before reporting frontend work done |

## Core Frontend Rules

- Build the real application surface first: job tables, filters, detail views,
  generated artifacts, approval flows, and run status.
- Use Ant Design Pro components for dense operational workflows.
- Do not present AI analysis as confirmed fact.
- Keep user approval explicit before resume submission or HR messaging.
- Preserve responsive layout for table-heavy pages; mobile can collapse into
  searchable cards or detail-first flows.

