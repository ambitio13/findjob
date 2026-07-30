# Bootstrap Task: Fill Project Development Guidelines

**You (the AI) are running this task. The developer does not read this file.**

The developer just ran `trellis init` on this project for the first time.
`.trellis/` now exists with empty spec scaffolding, and this bootstrap task
exists under `.trellis/tasks/`. When they want to work on it, they should start
this task from a session that provides Trellis session identity.

**Your job**: help them populate `.trellis/spec/` with the team's real
coding conventions. Every future AI session — this project's
`trellis-implement` and `trellis-check` sub-agents — auto-loads spec files
listed in per-task jsonl manifests. Empty spec = sub-agents write generic
code. Real spec = sub-agents match the team's actual patterns.

Don't dump instructions. Open with a short greeting, figure out if the repo
has any existing convention docs (CLAUDE.md, .cursorrules, etc.), and drive
the rest conversationally.

---

## Status (update the checkboxes as you complete each item)

- [x] Fill backend guidelines
- [x] Fill frontend guidelines
- [x] Add project-backed examples and initial code shapes

---

## Spec files to populate


### Backend guidelines

| File | What to document |
|------|------------------|
| `.trellis/spec/backend/directory-structure.md` | Where different file types go (routes, services, utils) |
| `.trellis/spec/backend/api-contracts.md` | FastAPI endpoint and async workflow contracts |
| `.trellis/spec/backend/database.md` | PostgreSQL, Redis, state, migrations, and query ownership |
| `.trellis/spec/backend/error-handling.md` | API, agent, tool, and cancellation failures |
| `.trellis/spec/backend/logging.md` | Structured logs, audit logs, metrics, and redaction |
| `.trellis/spec/backend/ai-sdk-integration.md` | Agent architecture, tool calling, RAG, and memory |
| `.trellis/spec/backend/quality.md` | Code review standards and future validation commands |


### Frontend guidelines

| File | What to document |
|------|------------------|
| `.trellis/spec/frontend/directory-structure.md` | Component/page/hook organization |
| `.trellis/spec/frontend/components.md` | Ant Design Pro component patterns |
| `.trellis/spec/frontend/table-detail-views.md` | Job table and per-record detail workflow |
| `.trellis/spec/frontend/hooks.md` | Custom hook naming, patterns |
| `.trellis/spec/frontend/state-management.md` | State library, patterns, what goes where |
| `.trellis/spec/frontend/type-safety.md` | TypeScript conventions, type organization |
| `.trellis/spec/frontend/api-integration.md` | API client, async agent-run, and error UX |
| `.trellis/spec/frontend/ai-sdk-integration.md` | Agent UI, generated artifacts, and approval flows |
| `.trellis/spec/frontend/quality.md` | Linting, testing, accessibility |

### Notes from this bootstrap pass

- The repository currently contains initiation documents, Trellis metadata, and
  no product source code.
- Existing spec files were a generic Next.js/oRPC/Drizzle scaffold and have
  been rewritten around the actual initiation direction: FastAPI, React, Ant
  Design Pro, PostgreSQL, Redis, Docker, and production-grade agent engineering.
- Agent framework decision for MVP: build a lightweight internal PiAgent-style
  layered runtime with planner, executor, reflector, tool registry, memory, and
  model gateway. Do not start with a high-level agent framework.
- Model access decision for MVP: use an OpenAI-compatible model gateway,
  initially configured for DeepSeek. Business code must not call provider SDKs
  directly.
- Product scope decision for MVP: JD is manually entered or imported by the
  user. Do not build platform automation yet.
- Resume output decision for MVP: generate JD analysis, resume optimization
  suggestions, rewrite snippets, HR opening messages, and skill-gap plans. A
  full customized resume export tool is deferred.
- Examples are based on project initiation artifacts and proposed initial source
  layout. After product code lands, specs should be revised with real file paths
  and source examples.


### Thinking guides (already populated)

`.trellis/spec/guides/` contains general thinking guides pre-filled with
best practices. Customize only if something clearly doesn't fit this project.

---

## How to fill the spec

### Step 1: Import from existing convention files first (preferred)

Search the repo for existing convention docs. If any exist, read them and
extract the relevant rules into the matching `.trellis/spec/` files —
usually much faster than documenting from scratch.

| File / Directory | Tool |
|------|------|
| `CLAUDE.md` / `CLAUDE.local.md` | Claude Code |
| `AGENTS.md` | Codex / Claude Code / agent-compatible tools |
| `.cursorrules` | Cursor |
| `.cursor/rules/*.mdc` | Cursor (rules directory) |
| `.windsurfrules` | Windsurf |
| `.clinerules` | Cline |
| `.roomodes` | Roo Code |
| `.github/copilot-instructions.md` | GitHub Copilot |
| `.vscode/settings.json` → `github.copilot.chat.codeGeneration.instructions` | VS Code Copilot |
| `CONVENTIONS.md` / `.aider.conf.yml` | aider |
| `CONTRIBUTING.md` | General project conventions |
| `.editorconfig` | Editor formatting rules |

### Step 2: Analyze the codebase for anything not covered by existing docs

Scan real code to discover patterns. Before writing each spec file:
- Find 2-3 real examples of each pattern in the codebase.
- Reference real file paths (not hypothetical ones).
- Document anti-patterns the team clearly avoids.

### Step 3: Document reality, not ideals

**Critical**: write what the code *actually does*, not what it should do.
Sub-agents match the spec, so aspirational patterns that don't exist in the
codebase will cause sub-agents to write code that looks out of place.

If the team has known tech debt, document the current state — improvement
is a separate conversation, not a bootstrap concern.

---

## Quick explainer of the runtime (share when they ask "why do we need spec at all")

- Every AI coding task spawns two sub-agents: `trellis-implement` (writes
  code) and `trellis-check` (verifies quality).
- Each task has `implement.jsonl` / `check.jsonl` manifests listing which
  spec files to load.
- The platform hook auto-injects those spec files + the task's `prd.md`
  into every sub-agent prompt, so the sub-agent codes/reviews per team
  conventions without anyone pasting them manually.
- Source of truth: `.trellis/spec/`. That's why filling it well now pays
  off forever.

---

## Completion

When the developer confirms the checklist items above are done with real
examples (not placeholders), guide them to run:

```bash
python3 ./.trellis/scripts/task.py finish
python3 ./.trellis/scripts/task.py archive 00-bootstrap-guidelines
```

After archive, every new developer who joins this project will get a
`00-join-<slug>` onboarding task instead of this bootstrap task.

---

## Suggested opening line

"Welcome to Trellis! Your init just set me up to help you fill the project
spec — a one-time setup so every future AI session follows the team's
conventions instead of writing generic code. Before we start, do you have
any existing convention docs (CLAUDE.md, .cursorrules, CONTRIBUTING.md,
etc.) I can pull from, or should I scan the codebase from scratch?"
