# Pre-Implementation Checklist

Run this checklist before implementing a feature.

## Scope

- Which product flow does this belong to: profile, resume parsing, job search,
  JD analysis, resume rewrite snippet generation, HR message generation,
  application tracking, HR follow-up, or interview preparation?
- Which layer owns the decision: frontend display, backend domain service,
  agent planner, tool adapter, database, cache, or worker?
- Is this feature only preparing an action, or can it perform an external
  side effect?

## Data

- What is the source of truth?
- Which IDs connect the user, resume version, JD, platform, generated artifact,
  and application record?
- Does the feature need provenance for AI output?
- Which fields are sensitive and must not appear in ordinary logs?

## Agent Safety

- What tool may the agent call?
- What input schema does the tool require?
- Is user approval required before execution?
- What happens on timeout, duplicate execution, or user cancellation?

## Verification

- What unit test proves the core rule?
- What integration or manual check proves the user workflow?
- What log or metric would help debug failure in production?
- Does the UI expose enough state for the user to understand what happened?
