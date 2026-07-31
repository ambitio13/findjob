# Structured Profile Text Fields — Implementation Plan

## 1. Backend profile schema contract

- [ ] Add typed named constraint fields to `UserProfileRead` /
      `UserProfileUpdate` or a companion schema, while preserving the underlying
      `constraints` column.
- [ ] Update `profile_service.update_profile` to map named fields into
      `constraints` keys instead of accepting arbitrary raw JSON from the UI.
- [ ] Preserve unknown legacy `constraints` keys and expose them as read-only
      legacy data if needed.
- [ ] Gate: backend profile tests cover named fields, salary validation,
      clearing fields, and legacy constraint preservation.

## 2. Apply resume profile draft

- [ ] Add `POST /api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft`.
- [ ] Preview mode (`confirm=false`) returns a diff without writing.
- [ ] Confirm mode writes allowed changes through `user_profile_repo.update_fields`.
- [ ] Non-empty current profile values are not overwritten unless
      `overwrite=true`.
- [ ] User-scoped 404 on cross-user resume/version access.
- [ ] Gate: backend tests cover preview, confirm, overwrite guard, overwrite
      allowed, missing facts, and cross-user 404.

## 3. Frontend profile form

- [ ] Replace the raw JSON constraints textarea with required and optional
      text-entry fields from `design.md`.
- [ ] Keep direct fields (`career_direction`, `base_location`, salary,
      strengths) visible and easy to edit.
- [ ] Render legacy unknown constraints outside the primary form if present.
- [ ] Gate: frontend lint/type/build pass.

## 4. Frontend draft apply UI

- [ ] On resume detail, show draft fields produced by resume extraction.
- [ ] Add "apply to profile" action with preview diff, confirm, and optional
      overwrite.
- [ ] Refresh `/users/me` after successful confirm.
- [ ] Gate: user can inspect what will change before writing.

## 5. JD analysis context

- [ ] Ensure `_profile_to_dict` and prompt rendering expose named fields clearly.
- [ ] Gate: JD-analysis context tests confirm named profile fields survive into
      the prompt context.

## Full validation

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 .trellis/scripts/task.py validate 07-31-structured-profile-text-fields
```
