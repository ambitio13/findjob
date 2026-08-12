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

## Semi-Auto Loop Safety

A "semi-auto loop" component (e.g. `RecommendedJobPilotPanel`) may automate
read-only / side-effect-free steps (inspect, match, prepare) in sequence, but
**must always stop** before any step with an external side effect (approve,
execute). This preserves the backend's "execute needs approval" invariant —
the frontend loop is just serial single-job API calls, not a batch path.

Implementation conventions:

- Use a top-level `Switch` to toggle semi-auto mode; disable it during `busy`.
- Use a `useRef` dedup key (`${applicationId}:${step}`) to prevent React
  StrictMode double-firing the same auto-step.
- Auto-stop (set `semiAuto` to `false`) on any failure or non-`communicate`
  decision, and surface the reason in an `Alert`.
- On a blocked decision (`needs_review` or `skip`), the frontend must
  **stay on the match step** — never advance to a step the backend will
  reject — and **hand over to the human-review area**: render it (the
  `!semiAuto` gate was a dead-end and is forbidden) and expand it on a
  loop-stop handover. The override is only ever submitted by an explicit
  human click, never by the auto effect (see
  `backend/evolution-contracts.md §12`). The backend match safety gate only
  downgrades (never upgrades; see `backend/evolution-contracts.md §2`), so a
  blocked result means non-override `prepare_communicate_action` returns
  422. Surface the interception (score / risks / missing requirements)
  verbatim from the backend — do not recompute or re-evaluate the decision
  in the frontend.

## Editable Content Boundary

- "Make generated content editable before approval" applies to artifacts the
  user regenerates and re-submits through a generation API (e.g. resume
  optimization suggestions, JD analysis notes).
- It does **not** apply to the match opening message: the `prepare` API reads
  the persisted match artifact by `match_artifact_id`, and the approval
  boundary is keyed on `payload_hash`. A frontend edit box for the opening
  message is a false promise — edits never reach the backend and would, if
  honored, bypass the `payload_hash` approval boundary. Show the opening
  message as read-only with a copy affordance; tell the user to re-match if
  they need a different message.

