# User Context, Profile, and Preferences

## Goal

Implement the MVP current-user context plus the user profile and job-search
preference workflow. This creates the ownership boundary required before resume
memory and resume-aware Agent analysis can work correctly.

## Background

The skeleton includes a `UserProfile` model and `/users/me` placeholder only.
The product needs this data before resume/JD analysis can be personalized.

Authentication can stay MVP-simple for now; the goal is user-scoped code with a
single current-user/demo-user flow, not production auth.

## Requirements

- Add Pydantic schemas for reading and updating the current user profile.
- Add `get_current_user()` dependency.
- Read `X-User-Id` when present; otherwise use a fixed development user such as
  `demo_user`.
- Auto-create the default user profile if it does not exist.
- Implement `/api/v1/users/me` GET and PUT/PATCH.
- Persist career direction, preferred locations/base, salary range, strengths,
  constraints, and target role information.
- Ensure future jobs, resumes, AgentRuns, artifacts, and application records can
  be assigned to the current user's `user_id`.
- Add frontend profile/settings page or panel with Ant Design Pro form.
- Validate salary ranges and required display/profile fields.
- Keep backend as source of truth; frontend should not invent profile schema.
- Provide tests for profile creation/update/read.

## Acceptance Criteria

- [ ] User can open a frontend profile/preferences UI.
- [ ] User can save and reload profile/preferences.
- [ ] Backend persists values in `user_profiles`.
- [ ] Backend has a reusable current-user dependency.
- [ ] Missing `X-User-Id` uses the deterministic demo user.
- [ ] Invalid salary range is rejected with a structured error.
- [ ] Existing `/users/me` placeholder is replaced by real behavior.
- [ ] Tests cover read, update, and validation failure.

## Out of Scope

- Multi-user auth provider integration.
- OAuth/social login.
- Admin user management.
- Platform account credentials.
