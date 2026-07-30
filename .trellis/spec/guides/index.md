# Thinking Guides

Use these guides before coding work that crosses layers or changes product
behavior.

| Guide | Purpose |
| --- | --- |
| [Pre-Implementation Checklist](./pre-implementation-checklist.md) | Confirm scope, data, permissions, and verification before coding |
| [Cross-Layer Thinking](./cross-layer-thinking-guide.md) | Trace a feature from user intent through UI, API, agent, storage, and logs |

## Project Bias

This product combines AI suggestions with real-world job-search actions. Future
code should be designed so a developer can answer:

- What did the agent know?
- Why did it recommend or prepare this action?
- Which user approved it?
- What external system did it touch?
- How can we replay, cancel, or explain the run?

