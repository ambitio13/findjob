# Prompt Template Extraction

## Goal

Move model prompt instructions into editable template files while preserving structured output contracts and existing tests.

## Requirements

- Extract the system prompt text for existing model-backed workflows into plain-text template files under the backend agent prompt area.
- Keep the runtime contract stable: existing prompt builder functions should still return the same `ChatMessage` shape and truncation metadata.
- Keep structured output schema descriptions close to the prompt builder code so schema/validation changes remain reviewed with code.
- Make templates easy for the project owner to edit without touching executor, service, or model gateway logic.
- Preserve fake-gateway routing markers and prompt version constants used by tests and local no-key development.
- Add focused tests that prove prompt builders load the editable template files and still include required safety / marker wording.

## Acceptance Criteria

- [ ] JD analysis, JD paste parsing, and resume fact extraction system prompts are read from editable template files.
- [ ] Prompt builder public APIs and returned `ChatMessage` objects remain compatible with existing executors.
- [ ] Tests pass for prompt contracts, including no-fabrication rules and fake-gateway routing markers.
- [ ] Backend style checks pass.
- [ ] The task is archived or left with clear follow-up notes after validation.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
