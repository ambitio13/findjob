# Job Platform Integration Strategy

## Current Status

The product should cover many resume-delivery platforms, but the initiation
materials leave open whether integrations are API-based, browser-automation
based, or agent-driven.

## Preferred Order

1. Official API or documented partner integration.
2. User-authorized import/export flow.
3. Browser automation behind platform-specific adapters.
4. Manual copy-ready drafts when automation is unsafe or unstable.

## Adapter Rules

- Each platform gets its own adapter with typed capabilities.
- Adapter methods must describe whether they are read-only or state-changing.
- State-changing methods require approval records and idempotency keys.
- Rate limits and anti-duplication behavior are part of the adapter contract.
- Platform-specific failures should map to shared backend error categories.

## Do Not Do

- Do not let a generic agent control arbitrary browser state without a sandbox.
- Do not mix platform selectors and scraping logic into product services.
- Do not submit resumes or messages during discovery or analysis steps.

