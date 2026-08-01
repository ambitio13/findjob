# Components

## Ant Design Pro

Use Ant Design Pro for operational screens:

- `ProTable` for job and application lists;
- `ProForm` for profile, job-search preference, and approval forms;
- `ProDescriptions` or detail panels for JD analysis and application records;
- `Steps`, `Timeline`, or status indicators for agent runs;
- modal confirmations for user approval gates.

## Component Boundaries

- Page components assemble layout, data hooks, and domain components.
- Domain components display business concepts such as `JobAnalysisPanel`,
  `ResumeVersionSelector`, `ApplicationStatusTag`, and `AgentRunTimeline`.
- Reuse `AgentRunStatusTag` (`@/features/agent-runs/AgentRunStatusTag`) for any
  agent-run status tag so colors and labels stay consistent.
- Shared components should be business-neutral: loading states, empty states,
  error boundaries, and layout wrappers.

## UX Rules

- Keep table columns scannable: platform, company, title, location/base, salary,
  match score, risk score, status, updated time, and next action.
- Use detail views for long JD text, resume diffs, generated messages, and
  analysis reasoning. In MVP, show resume rewrite snippets rather than a full
  generated resume document.
- Make generated content editable before approval.
- Use badges, tags, and progress states for AI work, but include enough text for
  accessibility and clarity.
- Avoid marketing-style landing pages for internal app surfaces.

## Anti-Patterns

- Do not trigger external platform actions from a plain table row click.
- Do not hide approval actions inside a generic chat message.
- Do not duplicate backend decision rules inside display components.
