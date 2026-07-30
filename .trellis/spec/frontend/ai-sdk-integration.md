# Agent UI Guidelines

The file name is kept for compatibility with existing Trellis manifests. It is
not a requirement to use a specific frontend AI SDK.

## Agent Run Display

Users should be able to understand what the agent is doing:

- current phase: planning, searching, analyzing, generating, waiting for
  approval, executing, finished;
- target job/platform;
- last successful step;
- failures and safe next actions;
- cancellation availability.

## Generated Content

- Show generated HR messages, resume optimization suggestions, and rewrite
  snippets as drafts.
- Let users edit drafts before approval.
- Clearly separate source facts from model-generated wording.
- Keep source JD, resume version, and generation metadata accessible from the
  detail view.
- Do not imply that MVP output is a complete finished resume file. The complete
  customized resume exporter is a later dedicated tool.

## Approval UI

Approval modals must show:

- target platform;
- target company and job title;
- resume version or generated artifact ID;
- exact HR message or generated snippet payload when applicable;
- consequence of approving the action.

Avoid generic "continue" buttons for irreversible actions.
