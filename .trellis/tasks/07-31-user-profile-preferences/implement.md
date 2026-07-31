# Implement — User Context, Profile, and Preferences

> Task: `07-31-user-profile-preferences`
> Prereq: `design.md` reviewed + `task.py start` run. Do NOT code before start.

## Execution Checklist (ordered)

### Phase A — Backend: current-user dependency & context

- [ ] A1. `backend/app/core/config.py`: add `demo_user_id: str = "demo_user"` to
      `Settings`. No new env var required (hard-coded default is intentional).
- [ ] A2. `backend/app/core/context.py` (NEW): define `_current_user_id:
      ContextVar[str | None]`, `set_current_user_id()`, `current_user_id()`,
      `clear_current_user_id()`. `clear_current_user_id()` should clear via
      `set(None)` instead of token reset because FastAPI/TestClient cleanup can
      run in a different `contextvars.Context`. Module docstring explains it is a
      request-scoped read-only convenience mirror, not the primary injection
      path.
- [ ] A3. `backend/app/db/repositories/__init__.py` (NEW, empty package).
- [ ] A4. `backend/app/db/repositories/user_profile_repo.py` (NEW):
      - `get(db, user_id) -> UserProfile | None`
      - `ensure_default(db, user_id) -> UserProfile` — INSERT with
        `display_name="演示用户"` on absent; on conflict do nothing, then SELECT
        and return existing. Use SQLAlchemy core insert for Postgres
        (`insert().on_conflict_do_nothing`) guarded by a try/except fallback to
        get-then-create for SQLite/test. Do not touch `updated_at` on existing
        rows during GET requests.
      - `update_fields(db, user, fields_dict) -> UserProfile` — apply dict,
        flush, refresh.
- [ ] A5. `backend/app/api/deps.py`: add `get_current_user()` dependency per
      design §4.4. Import `Header`, `Annotated`, `Generator`. Set the ContextVar
      and clear it in `finally`.

### Phase B — Backend: schemas & service

- [ ] B1. `backend/app/schemas/user.py` (NEW): `UserProfileRead`,
      `UserProfileUpdate` per design §4.3. Include `model_validator` salary
      check. Use `EmailStr` (ensure `email-validator` is in deps; if not, add
      to `pyproject.toml` and re-lock — check first).
- [ ] B2. `backend/app/services/profile_service.py` (NEW):
      - `read_profile(db, user) -> UserProfileRead`
      - `update_profile(db, user, payload: UserProfileUpdate) -> UserProfileRead`
        using `payload.model_dump(exclude_unset=True)` so omitted fields are
        untouched and explicit `None` clears nullable columns.
- [ ] B3. Verify `pyproject.toml` deps include `email-validator` (pydantic
      `[email]` extra). If missing, add `email-validator` and reinstall. Do
      NOT add pydantic extras we don't use.

### Phase C — Backend: router

- [ ] C1. Replace `backend/app/api/v1/users.py` placeholder with:
      - `GET /me` → `profile_service.read_profile`
      - `PATCH /me` → `profile_service.update_profile`
      - Both depend on `get_current_user` + `get_db_session`.
- [ ] C2. Ensure `router.py` still includes `users.router` (no change expected).
- [ ] C3. Update existing `backend/app/api/v1/jobs.py`:
      - `POST /jobs` depends on `get_current_user` and writes
        `JobPosting.user_id = current_user.id`.
      - `GET /jobs` depends on `get_current_user` and filters by
        `JobPosting.user_id == current_user.id`.
      - `GET /jobs/{job_id}` depends on `get_current_user` and returns 404 if
        the job is missing or belongs to another user.

### Phase D — Backend: tests

- [ ] D1. `backend/app/tests/test_user_profile.py` (NEW) covering design §10:
      1. GET /me no header → 200, id `demo_user`, row created.
      2. GET /me `X-User-Id: custom_user` → 200, id `custom_user`.
      3. PATCH /me update display_name + salary → 200, persisted on re-GET.
      4. PATCH /me `salary_min=200, salary_max=100` → 422.
      5. Idempotency: two GET /me → same id, one row.
      6. Idempotency: repeated GET does not change `updated_at`.
      7. JSON fields (strengths list, constraints dict) round-trip.
- [ ] D2. Extend or add jobs ownership tests:
      1. POST /jobs with no header binds `user_id == "demo_user"`.
      2. POST /jobs with `X-User-Id: custom_user` binds `custom_user`.
      3. GET /jobs only lists current user's jobs.
      4. GET /jobs/{job_id} returns 404 for another user's job.
- [ ] D3. `conftest.py`: ensure TestClient fixture covers the new routes (no
      change expected — existing fixture spins the app with fake provider).

### Phase E — Backend validation gate

- [ ] E1. `cd backend && .venv/bin/ruff check .` → all passed.
- [ ] E2. `cd backend && .venv/bin/pytest -q` → all passed (existing 4 + new).
- [ ] E3. Grep guard: no forbidden scope terms (`自动投递|auto_submit|
      browser_automation|selenium|playwright|完整简历导出|自动发送HR|复杂RAG`)
      in new/changed files.

### Phase F — Frontend

- [ ] F1. `frontend/src/types/index.ts`: add `UserProfile` + `UserProfileUpdate`
      mirroring backend schemas.
- [ ] F2. `frontend/src/api/client.ts`: add `getCurrentUser()` and
      `updateCurrentUser(payload)`; reuse existing axios instance + error
      handling.
- [ ] F3. `frontend/src/features/profile/ProfileForm.tsx` (NEW): `ProForm` with
      two grouped sections (基本信息 / 求职偏好) per design §9. Fields: see
      design. Use `ProFormText/ProFormSelect/ProFormDigit/ProFormTags`.
- [ ] F4. `frontend/src/pages/profile/ProfilePage.tsx` (NEW): page wrapper that
      renders `<ProfileForm />` inside a ProCard.
- [ ] F5. `frontend/src/components/layout/AppLayout.tsx`: add menu item "我的画像"
      → `/profile`.
- [ ] F6. `frontend/src/main.tsx`: add `/profile` route → `ProfilePage`.

### Phase G — Frontend validation gate

- [ ] G1. `cd frontend && pnpm lint` → clean.
- [ ] G2. `cd frontend && pnpm type-check` → clean.
- [ ] G3. `cd frontend && pnpm build` → built successfully (warnings OK, errors no).

### Phase H — Spec & context manifests

- [ ] H1. Verify `implement.jsonl` / `check.jsonl` contain only real spec
      entries. `directory-structure.md` should be present in implement.jsonl for
      the new repositories package.
- [ ] H2. Note any new pattern for Phase 3.3 spec update (e.g. "current-user
      dependency pattern", "contextvar mirror convention") — decide at finish.

## Validation Commands Summary

```bash
# Backend
cd backend
.venv/bin/ruff check .
.venv/bin/pytest -q

# Frontend
cd frontend
pnpm lint
pnpm type-check
pnpm build

# Scope guard
grep -rniE '自动投递|auto_submit|browser_automation|selenium|playwright|完整简历导出|自动发送HR|复杂RAG' backend/app frontend/src || echo "CLEAN"
```

## Rollback Points

- After Phase A–C (before tests): if schema/service doesn't compile, revert
  `users.py`, `deps.py`, new files. No DB migration to undo.
- After Phase D (tests fail): fix in-place; if a design assumption is wrong
  (e.g. upsert semantics), return to `design.md` §4.4, revise, re-run.
- Frontend (Phase F–G): all additive; revert files individually if build breaks.
- Whole task: single revert of the commit (no migration, no env change).

## Out-of-Scope Reminders (do NOT implement)

- No resume upload / parsing.
- No JD analysis agent / model calls.
- No auth / OAuth / login UI.
- No platform automation, auto-submission, HR messaging, browser automation.
- No complete resume export, no complex RAG.
- No new DB migration (existing schema suffices).

## Done Definition

- All Phase A–G checkboxes ticked.
- `design.md` decisions honored (demo_user upsert, salary validator,
  `/profile` page, ContextVar mirror with cleanup, current-user job scoping).
- Validation commands all green.
- Ready for Phase 3.3 (spec update review) → 3.4 (commit) → `/trellis:finish-work`.
