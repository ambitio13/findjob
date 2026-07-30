# Shared Code Quality

## Scope

Use this guide for all future backend, frontend, worker, agent, and tooling
code. The repository currently contains planning documents only, so these rules
define the baseline until real source files establish tighter local patterns.

## Source-Backed Product Constraints

The project must support resume parsing, job discovery, JD analysis, tailored
resume/message generation, application tracking, and interview preparation.
These flows are defined in `项目立项/业务.md` and must be treated as one connected
domain rather than isolated screens.

## Quality Rules

- Design around explicit state transitions. Examples: job discovered, analyzed,
  approved for application, resume generated, message generated, sent, replied,
  rejected, interview planned.
- Do not hide irreversible actions behind a generic agent response. Submission
  and messaging code must have a typed request, permission check, audit log, and
  retry policy.
- Keep business logic out of UI components and transport handlers. Use service
  or domain modules for scoring jobs, building prompts, computing skill gaps,
  and deciding next actions.
- Add tests around decision logic before optimizing UI polish or model prompts.
- Avoid duplicating domain rules between backend and frontend. The backend owns
  durable rules; the frontend may mirror display-only labels and filters.
- Prefer clear data contracts over implicit dictionaries or loosely shaped JSON.

## Review Checklist

- Does the change preserve user control over external job-platform actions?
- Are AI outputs validated, stored with provenance, and safe to re-render?
- Are sensitive fields excluded from casual logs and screenshots?
- Can a failed or cancelled long-running agent task be resumed or explained?
- Are table and detail views backed by stable IDs rather than display text?
- Is there a test or documented manual check for the user-visible workflow?

