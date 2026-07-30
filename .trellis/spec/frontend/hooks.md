# Hooks

## Naming

- Data reads: `useJobs`, `useJobDetail`, `useApplications`,
  `useAgentRun`.
- Mutations: `useCreateAgentRun`, `useApproveApplicationAction`,
  `useGenerateResumeArtifact`.
- Local UI state: use specific names such as `useJobFilters` or
  `useApprovalModal`.

## Rules

- Keep API calls in hooks or API modules, not deeply inside components.
- Keep mutation side effects explicit: invalidate relevant queries, show
  feedback, and update the route or selected record when needed.
- Do not combine unrelated domains in one hook.
- Preserve async agent-run state: queued, running, waiting for approval,
  succeeded, failed, cancelled.

## Testing

When hook behavior includes status transitions or cache invalidation, add a test
once frontend test tooling exists.

