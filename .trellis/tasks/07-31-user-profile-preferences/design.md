# Design — User Context, Profile, and Preferences

> Task: `07-31-user-profile-preferences`
> Status: planning (do not implement before `task.py start`)

## 1. Goal & Scope

Establish the MVP current-user context and the user-profile / job-search-preference
workflow. This creates the ownership boundary that later tasks (resume upload, JD
analysis agent) will rely on.

**In scope**

- `get_current_user()` dependency reading `X-User-Id`; default `demo_user`.
- Auto-upsert of the demo/current user profile on first request.
- `GET /api/v1/users/me` and `PATCH /api/v1/users/me` for partial profile
  updates.
- Pydantic schemas for read + update with salary-range validation.
- Frontend `/profile` page with Ant Design Pro form + sidebar menu entry.
- A reusable `user_id` propagation path so future jobs/resumes/agent_runs/
  artifacts can be bound to the current user.
- Existing job APIs must become user-scoped in this task: manual JD creation
  binds `JobPosting.user_id` to the current user; job list/detail only return
  records owned by the current user.

**Out of scope (preserve verbatim from PRD)**

- Multi-user auth provider integration, OAuth/social login, admin user management,
  platform account credentials.
- Resume upload/parsing (next task), JD analysis agent (task #3).
- Platform automation, automatic resume submission, automatic HR messaging,
  browser automation, complete resume export, complex RAG.

## 2. Decisions (confirmed with user)

| Decision | Choice | Rationale |
|---|---|---|
| current_user delivery | `Header` + FastAPI `Depends`, plus a request-scoped `contextvars.ContextVar` mirror with cleanup | Smallest change to existing `deps.py` style; services that already accept a `Session` via Depends can also accept `current_user`. The `ContextVar` is a convenience for non-route code, but it must be cleared after each request to avoid leaking user context. |
| demo_user resolution | `X-User-Id` header if present and non-empty; else literal `"demo_user"` | Matches PRD. No auth, no token parsing. |
| Auto-create profile | Ensure row exists without touching existing rows: `INSERT ... ON CONFLICT DO NOTHING`, then SELECT | First request just works; concurrent requests safe via DB unique PK. Read requests must not update `updated_at`. |
| Salary validation | `salary_min <= salary_max` when both present; both nullable | Matches PRD "validate salary ranges" + acceptance "invalid salary range rejected with structured error". |
| Frontend form | New `/profile` route + sidebar menu item "我的画像", `ProForm` with two grouped sections (基本信息 / 求职偏好) | Follows `frontend/components.md` ("ProForm for profile, job-search preference"). |

## 3. Module Boundaries

```
backend/app/
  core/
    config.py          # + DEMO_USER_ID constant (no new env var; hard-coded default)
    context.py         # NEW: user_id ContextVar + get/set/clear helpers
  db/
    models/models.py   # unchanged (UserProfile already has the needed columns)
    repositories/
      __init__.py      # NEW package
      user_profile_repo.py  # NEW: get, upsert, update
  schemas/
    user.py            # NEW: UserProfileRead / UserProfileUpdate (+ SalaryRange validator)
    api.py             # unchanged (health/jobs/agent-runs stay)
  services/
    profile_service.py # NEW: read_profile, update_profile (owns validation + repo calls)
  api/
    deps.py            # + get_current_user() dependency
    v1/
      users.py         # replace placeholder with real GET/PATCH /users/me
      jobs.py          # update existing jobs endpoints to bind/filter user_id
  tests/
    test_user_profile.py  # NEW
```

Frontend:

```
frontend/src/
  api/client.ts        # + getCurrentUser / updateCurrentUser
  types/index.ts       # + UserProfile / UserProfileUpdate
  features/profile/
    ProfileForm.tsx    # NEW: ProForm, two grouped sections
  pages/profile/
    ProfilePage.tsx    # NEW: page wrapper
  components/layout/AppLayout.tsx  # + "我的画像" menu route
  main.tsx             # + /profile route
```

## 4. Contracts

### 4.1 `GET /api/v1/users/me`

- Header: optional `X-User-Id`.
- 200 → `UserProfileRead`.
- Resolves current user (upsert demo profile if missing) before reading.

### 4.2 `PATCH /api/v1/users/me`

- Header: optional `X-User-Id`.
- Body: `UserProfileUpdate` (all fields optional; `null` clears a field where
  the underlying column is nullable).
- 200 → `UserProfileRead` (post-update state).
- 422 → structured validation error (e.g. `salary_min > salary_max`).
- 404 → only if the current user truly cannot be resolved (defensive; upsert
  makes this near-impossible — kept as a safety net).

### 4.3 Schemas (`schemas/user.py`)

```python
class UserProfileRead(BaseSchema):
    id: str
    display_name: str
    email: str | None = None
    career_direction: str | None = None
    base_location: str | None = None
    preferred_locations: list[str] | None = None   # JSON list in DB
    salary_min: int | None = None
    salary_max: int | None = None
    strengths: list[str] | None = None             # JSON list in DB
    constraints: dict[str, Any] | None = None      # flexible JSON
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserProfileUpdate(BaseModel):
    display_name: str | None = None
    email: EmailStr | None = None
    career_direction: str | None = None
    base_location: str | None = None
    preferred_locations: list[str] | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    strengths: list[str] | None = None
    constraints: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_salary_range(self) -> "UserProfileUpdate":
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must be <= salary_max")
        return self
```

Notes:
- `preferred_locations` / `strengths` are stored as JSON in the existing
  `JSON` columns; the schema exposes them as `list[str]` for ergonomics.
  `constraints` stays `dict[str, Any]` (free-form).
- `display_name` is required on the model but optional in `UserProfileUpdate`;
  an all-`None` PATCH is a valid no-op (returns current state).

### 4.4 `get_current_user()` dependency

```python
def get_current_user(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    db: Session = Depends(get_db_session),
) -> Generator[UserProfile, None, None]:
    user_id = (x_user_id or "").strip() or settings.demo_user_id
    user = user_profile_repo.ensure_default(db, user_id)
    set_current_user_id(user.id)  # ContextVar mirror
    try:
        yield user
    finally:
        clear_current_user_id()
```

- `settings.demo_user_id` = `"demo_user"` (hard-coded default; no new env var
  needed, but exposed on `Settings` so tests/override can change it).
- `ensure_default` inserts a row with `display_name="演示用户"` if absent and
  selects the row. It must not update an existing row during GET requests.
- The `ContextVar` is set here so downstream services/repositories can read
  `current_user_id()` without an extra parameter — but route handlers MUST
  still take the `UserProfile` via Depends for explicit ownership (the
  ContextVar is a convenience, not the primary path). The dependency must clear
  the ContextVar in `finally`.
- Prefer `clear_current_user_id()` (`set(None)`) over token-based
  `ContextVar.reset(token)` for this FastAPI/TestClient stack: cleanup can run
  in a different `contextvars.Context`, where token reset raises `ValueError`.

### 4.5 Existing Jobs API User Scope

Current job endpoints already exist and must be user-scoped now:

- `POST /api/v1/jobs`: depends on `get_current_user`; creates
  `JobPosting(user_id=current_user.id, ...)`.
- `GET /api/v1/jobs`: depends on `get_current_user`; filters
  `JobPosting.user_id == current_user.id`.
- `GET /api/v1/jobs/{job_id}`: depends on `get_current_user`; returns 404 when
  the job does not exist or belongs to another user.

This is required before resume-aware JD analysis. Otherwise future Agent runs
could analyze a JD that has no owner.

## 5. Data Flow

```
Client --[X-User-Id?]--> GET/PATCH /users/me
                            │
                get_current_user (Depends)
                            │
            resolve user_id (header or demo_user)
                            │
            user_profile_repo.ensure_default(db, user_id)
                   │                      │
            (insert-if-missing)    set ContextVar user_id
                            │
                    UserProfile row
                            │
        ┌───────────────────┴───────────────────┐
   GET: profile_service.read_profile(db, user)   PATCH: profile_service.update_profile(db, user, body)
                            │                                   │
                            └──────► UserProfileRead ◄──────────┘
```

PATCH flow detail:
1. `get_current_user` ensures + returns the `UserProfile` row.
2. `profile_service.update_profile` applies `UserProfileUpdate` to the row
   (only non-`None` fields; `null` is treated as "clear" for nullable columns
   — implemented by distinguishing "field absent" from "field null" via
   `model_dump(exclude_unset=True)`).
3. Repo flushes + refreshes; service returns `UserProfileRead`.

Job flow detail:
1. Jobs route depends on `get_current_user`.
2. Create path writes `user_id=current_user.id`.
3. List/detail paths filter by `user_id=current_user.id`.
4. Cross-user access returns 404, not 403, to avoid revealing resource
   existence.

## 6. Database & Migration

- **No schema change.** The existing `UserProfile` model and `0001_initial`
  migration already cover all needed columns (`display_name`, `email`,
  `career_direction`, `base_location`, `preferred_locations` JSON,
  `salary_min/max`, `strengths` JSON, `constraints` JSON, timestamps).
- The ensure-default path uses PostgreSQL `INSERT ... ON CONFLICT DO NOTHING`,
  then SELECT. For SQLite/test-fallback the repo falls back to get-then-insert
  inside the same transaction.
- Existing `job_postings.user_id` is already nullable. This task should start
  writing it for new records and filtering by it. No migration is required yet.
- **No new migration file is added in this task.**

## 7. Error Handling

| Case | HTTP | Body |
|---|---|---|
| `salary_min > salary_max` | 422 | FastAPI validation error envelope (`detail` list) |
| Invalid email | 422 | Pydantic validation error |
| Current user cannot be resolved (defensive) | 404 | `{"detail": "user profile not found"}` |
| DB down | 500 | generic error (existing pattern) |

Follows `backend/error-handling.md`: structured codes, no stack trace leak.
The salary validator raises `ValueError` inside a Pydantic `model_validator`,
which FastAPI surfaces as a 422 automatically — no custom exception handler
needed for MVP.

## 8. Logging & Redaction

- Log `user_id` on profile read/update (structured, per `logging.md`).
- Do **not** log full `constraints` / `strengths` payloads (may contain
  sensitive career context). Log field names + lengths only.
- No resume content, no credentials — none involved here.

## 9. Frontend Design

- `ProfileForm.tsx`: `ProForm` with two `ProFormGroup` sections:
  - 基本信息: `display_name` (ProFormText, required), `email` (ProFormText email),
    `career_direction` (ProFormSelect, options from
    `JobSearchDirection`), `base_location` (ProFormText).
  - 求职偏好: `preferred_locations` (ProFormTags or Select multiple),
    `salary_min` / `salary_max` (ProFormDigit pair), `strengths` (ProFormTags),
    `constraints` (key/value — MVP: a single `ProFormTextArea` with JSON, or
    skip if empty; keep simple).
- On mount: `GET /users/me` → fill form.
- On submit: `PATCH /users/me` → message success + refresh.
- Error UX: field-level errors from 422 (`apiErrorMessage` already exists).
- Menu: add "我的画像" (`/profile`) to `AppLayout` route list, after "职位".

## 10. Testing Plan

Backend (`tests/test_user_profile.py`):
1. `GET /users/me` with no header → 200, `id == "demo_user"`, profile created.
2. `GET /users/me` with `X-User-Id: custom_user` → 200, `id == "custom_user"`.
3. `PATCH /users/me` updating `display_name` + `salary_min/max` → 200, persisted.
4. `PATCH /users/me` with `salary_min=200, salary_max=100` → 422.
5. Repeated `GET /users/me` is idempotent (no duplicate rows).
6. Repeated `GET /users/me` does not change `updated_at`.
7. `constraints`/`strengths` round-trip (JSON list/dict).
8. `POST /jobs` binds the new job to current user.
9. `GET /jobs` only lists current user's jobs.
10. `GET /jobs/{job_id}` returns 404 for another user's job.

Frontend: existing `pnpm lint/type-check/build` must stay green; no new test
harness required for MVP (per `frontend/quality.md` current state).

## 11. Tradeoffs & Risks

- **ContextVar vs explicit param**: ContextVar is convenient but can be a
  hidden coupling. Mitigation: route handlers always take `UserProfile` via
  Depends (explicit); ContextVar is read-only convenience for repos/workers.
- **Ensure-default on every request**: adds a DB read and possible insert per
  current-user request. Acceptable for MVP; can be cached in Redis later (out of
  scope). Existing rows must not be touched on read.
- **No real auth**: `X-User-Id` is trivially spoofable. Explicitly MVP-only;
  PRD forbids auth provider work. Documented in README/spec.
- **`null` vs absent in PATCH**: using `exclude_unset=True` means a field the
  client omits is left untouched; a field sent as `null` clears it. This is
  the least surprising behavior and matches Pydantic conventions.

## 12. Rollout / Rollback

- Rollout: pure additive (new files + replaced placeholder router). No DB
  migration. Deploy = container rebuild.
- Rollback: revert the commit; the placeholder `users.py` returns. Existing
  rows in `user_profiles` remain valid (no schema change).
