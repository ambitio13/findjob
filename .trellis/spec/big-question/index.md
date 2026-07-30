# Big Questions

These files capture architecture decisions that are important but not fully
settled by the initiation documents.

| File | Question |
| --- | --- |
| [agent-framework-selection.md](./agent-framework-selection.md) | Which agent framework or orchestration approach should the project use? |
| [platform-integration-strategy.md](./platform-integration-strategy.md) | How should job platforms be connected safely and maintainably? |
| [agent-safety-sandbox.md](./agent-safety-sandbox.md) | How do we prevent unauthorized or unsafe tool actions? |
| [rag-memory-design.md](./rag-memory-design.md) | How should memory, RAG, and retrieval be split across stores? |
| [postgres-json-jsonb.md](./postgres-json-jsonb.md) | When should flexible payloads use JSONB instead of normal columns? |

## Rules for Resolving Big Questions

- Start from the product flow and risk, not from framework popularity.
- Record tradeoffs and the final decision in the relevant file.
- Update backend, frontend, and shared specs after a decision changes concrete
  implementation rules.

