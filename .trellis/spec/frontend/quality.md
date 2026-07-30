# Frontend Quality Checklist

Run the relevant checks once frontend tooling exists. Until then, use this file
as the review checklist for UI design and implementation.

## Required Checks

- Screens support the core job table and per-job detail workflow.
- User approvals show exact target, artifact, and consequence.
- Generated content is editable before external execution.
- Loading, empty, error, failed, cancelled, and waiting-for-approval states are
  implemented.
- Table filters and selected records survive refresh through URL or backend
  state where appropriate.
- Sensitive data does not leak into analytics or casual logs.
- Components use Ant Design Pro patterns for operational workflows.

## Expected Commands

After package manifests exist, keep this section updated with the actual project
commands. Good targets are:

```bash
pnpm lint
pnpm type-check
pnpm test
pnpm build
```

