# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] — Phase 1

### Task 1 — Project skeleton
- Initialized monorepo structure (`backend/`, `frontend/`, `docs/`).
- Backend: FastAPI app factory, Pydantic Settings config, async
  SQLAlchemy engine/session, Alembic configured for async migrations,
  `GET /api/v1/health` endpoint (real DB round-trip check).
- Frontend: React + Vite + TypeScript scaffold, minimal API client,
  health-check verification screen.
- Docker Compose (backend + frontend + postgres/pgvector) for local dev.
- Root `.gitignore`, `README.md`, `.env.example` for both services.
- Verified: backend installs and boots via uvicorn, `/docs` and
  `/api/v1/health` respond correctly (503 when DB unreachable, as
  designed); frontend installs, type-checks, builds, and serves via
  `npm run dev`.

### Task 2.1 — Database foundation
- Added `app/models/base.py`: `TimestampMixin` (DB-generated
  `created_at`/`updated_at`) and `BaseModel` (UUID primary key +
  `TimestampMixin`), built on top of the existing `Base` in
  `app/db/session.py`.
- Added `app/models/user.py`: `User` model — `id`, `email`,
  `username`, `hashed_password`, `is_active`, `is_superuser`,
  `created_at`, `updated_at`. Schema only, no auth logic.
- Updated `app/models/__init__.py` to import all models, and
  `alembic/env.py` to import `app.models` — this is how Alembic
  autogenerate discovers new models going forward with zero further
  env.py changes.
- Generated and applied migration `0618947abd34_create_users_table`.
- Verified against a real (non-Docker, locally installed) Postgres 16
  instance: migration applies, table schema matches spec exactly,
  rollback drops the table cleanly, re-upgrade recreates it,
  `/api/v1/health` reports `"database": "ok"`, and `updated_at`
  correctly auto-refreshes on ORM-mediated updates (confirmed it does
  *not* fire on raw SQL updates — documented as a known limitation of
  SQLAlchemy's `onupdate`, which is application-level, not a DB trigger).

### Task 2.2 — Authentication foundation
- Added `app/core/security.py`: `hash_password`/`verify_password`
  (bcrypt, direct library not passlib) and `create_access_token`/
  `decode_access_token` (PyJWT). Not yet wired into any route
  dependency — `get_current_user` / protected routes are a later task.
- Added `app/schemas/auth.py`: `SignupRequest`, `LoginRequest`,
  `UserPublic`, `TokenResponse`. Password validated for both minimum
  length and bcrypt's 72-byte limit (byte length, not char length —
  see note below).
- Added `app/services/auth_service.py`: `create_user`,
  `authenticate_user`. Login failure is the same error for
  "no such user", "wrong password", and "inactive user" to avoid
  revealing which case applies; `authenticate_user` always performs a
  bcrypt comparison (against a dummy hash when no user is found) to
  avoid a timing side-channel revealing whether an email is registered.
- Added `app/api/auth.py`: `POST /auth/signup`, `POST /auth/login`.
- Added `jwt_secret_key`, `jwt_algorithm`,
  `jwt_access_token_expire_minutes` to `app/core/config.py`.
- Added dependencies: `bcrypt`, `pyjwt`, `email-validator` (the last
  one wasn't in the original plan — needed by Pydantic's `EmailStr`,
  flagged and added during implementation).
- Added `tests/conftest.py` + `tests/test_auth.py` — 12 tests covering
  password hashing, JWT round-trip, tampered-token rejection, signup
  (success, duplicate email, duplicate username, short password, and
  a multi-byte-character password that exceeds bcrypt's byte limit
  while under the schema's character limit), and login (success,
  wrong password, nonexistent user).
- CI: added a Postgres service container to the backend job and
  switched from an import-only smoke test to running the real test
  suite (`pytest`).
- Two bugs found and fixed during manual verification (not just
  written and assumed correct):
  1. Test engine created at module import time caused
     "another operation is in progress" errors across tests, because
     asyncpg connections are bound to the event loop they were opened
     in and pytest-asyncio gives each test its own loop. Fixed by
     creating the engine inside a function-scoped fixture.
  2. A password of 72 *characters* using multi-byte UTF-8 characters
     (e.g. `"é" * 72` = 144 bytes) passed schema validation but then
     raised an unhandled error in `security.py`, surfacing as a 500.
     Fixed with an explicit byte-length validator on the schema field,
     with a regression test added.

### Task 2.3 — Frontend authentication
- Added `src/constants/routes.ts`: centralized route paths.
- Added `src/types/auth.ts`: types mirroring the backend auth schemas.
- Added `src/services/auth.ts`: isolated `signup()`/`login()` API calls.
- Added `src/context/AuthContext.tsx` (context + provider) and
  `src/hooks/useAuth.ts` (consumption) — split per review feedback.
  Persists the access token (and the email used to obtain it) to
  localStorage; documented trade-off vs. httpOnly cookies (would
  require backend changes, out of scope for a frontend-only task).
- Added `src/pages/LoginPage.tsx`, `src/pages/SignupPage.tsx`.
- Modified `src/App.tsx`: routing shell (`BrowserRouter` + `AuthProvider`
  + three routes) with an inline (not a separate page) authenticated/
  unauthenticated placeholder at `/`, per review feedback rejecting a
  temporary `HomePage.tsx`.
- Modified `src/services/api.ts`: added `post<T>()` alongside the
  existing `get<T>()`, plus shared error-message extraction handling
  both FastAPI's `{detail: string}` and `{detail: [{msg}]}` shapes.
- Added dependency: `react-router-dom`.
- Design decision: signup doesn't return a token (backend returns
  `UserPublic`, not `TokenResponse`), so `AuthContext.signup()` calls
  `login()` immediately after a successful signup with the same
  credentials, to land the user in an authenticated state at `/` as
  specified. No backend changes; reuses the existing login endpoint.
- Verified with a real Chromium browser (Playwright), not just unit
  logic: signup → auto-login → redirect to `/` with correct email
  displayed and token in localStorage; logout clears state; login
  with the same credentials works; wrong password shows "Invalid
  email or password" without crashing; duplicate-email signup shows
  "Email already registered" and stays on the signup page.
- `npm audit`: `react-router-dom@latest` (7.18.2) has one high-severity
  advisory specific to RSC (React Server Components) mode, which this
  app doesn't use (plain client-side `BrowserRouter`, no loaders,
  actions, or SSR). Pinning to an older version to "fix" it was tried
  and reverted — it reintroduced 13 other vulnerabilities that predate
  that version's fixes. Latest is the safer choice here.

### Task 3A — get_current_user protected-route dependency
- Added `app/core/deps.py`: `get_current_user()` — the project's
  first protected-route dependency. Verifies a bearer JWT via the
  existing `decode_access_token()`, loads the `User`, uniform 401 on
  any failure (missing/invalid/expired token, malformed subject,
  nonexistent or inactive user).
- Key decision: `HTTPBearer`, not `OAuth2PasswordBearer` — `/auth/login`
  uses a JSON body, not the OAuth2 password-flow's form-encoded
  contract.
- Added `tests/test_deps.py` — 5 tests, called directly (no route
  exists yet to test it through). 17/17 backend tests passing overall.
- Not yet consumed by any route — verified `GET /api/v1/documents`
  returns 404, not 401, confirming zero accidental footprint. Also
  manually verified against the real dev database (not just the test
  DB) with a real signed-up user and token.
- Deliberately scoped as an independent milestone, completed and
  approved before any Task 3B (document management) code began.

### Task 3B — Checkpoint 1: Document data layer
- Added `app/models/document.py`: `Document` model — `user_id` (FK →
  `users.id`, `ON DELETE CASCADE`, indexed), `original_filename`
  (`VARCHAR(255)`), `stored_filename` (`VARCHAR(64)`, unique),
  `content_type` (`VARCHAR(100)`), `file_size_bytes` (`BIGINT`),
  `storage_path` (`VARCHAR(500)`), plus `created_at`/`updated_at` via
  `BaseModel`. No `status` column (see Task 3B planning — no async
  pipeline exists yet).
- Deliberately **no `relationship()`** on either `User` or `Document`
  this checkpoint. Reason (documented in-code on the `user_id`
  column): this project uses async SQLAlchemy throughout, where a
  default (`lazy="select"`) relationship raises `MissingGreenlet` at
  runtime if accessed outside an explicit `await` — a real footgun
  for whoever first writes `current_user.documents` in a route
  handler with no reason to expect it. Deferred to the checkpoint
  that first has a real consumer, so the loading strategy
  (`lazy="selectin"` or `AsyncAttrs.awaitable_attrs`) is chosen
  deliberately rather than guessed at in a vacuum.
- Generated, reviewed, and applied the `documents` table migration
  (`23fed3dde01d_create_documents_table`).
- Added `tests/test_document_model.py` — 5 model-level tests (no
  HTTP, no service layer): insert/retrieve, timestamp
  auto-population, `stored_filename` uniqueness, cascade delete on
  owning user, `user_id` NOT NULL. 22/22 backend tests passing
  overall (17 pre-existing + 5 new), zero regressions.
- Verified against a real local Postgres instance (not just the test
  DB): inserted/updated/queried a real row, confirmed the unique
  constraint rejects a duplicate `stored_filename`, confirmed
  `ON DELETE CASCADE` actually removes a document when its owning
  user is deleted (checked directly via `psql`), and confirmed
  `alembic downgrade -1` / `upgrade head` both apply cleanly.
- Scope strictly limited to the data layer per the approved
  checkpointed plan — no storage service, no upload endpoint, no API
  route, no frontend change. `get_current_user` remains unconsumed by
  any route.

### Task 3B — Checkpoint 2: Storage service
- Added `app/services/storage_service.py`: pure file-I/O service with
  no DB or FastAPI `UploadFile` dependency — `save_file()` takes raw
  `bytes` + `original_filename` + `content_type`, so it's testable and
  reusable independent of the web layer.
- `SavedFile` (frozen dataclass) return type — field names match the
  `Document` model's columns 1:1 (`stored_filename`, `storage_path`,
  `original_filename`, `content_type`, `file_size_bytes`) so a future
  document service can construct a `Document` from it without
  renaming anything.
- UUID-based stored filenames (`generate_stored_filename`) — never
  derived from user input, avoiding path-traversal risk and
  guaranteeing no collisions.
- Extension-only validation (`validate_extension`) against
  `settings.allowed_upload_extensions_list` — matches the
  already-documented decision to skip `python-magic` content-sniffing
  for Phase 1 (see Section 11 #18). A rejected extension raises before
  the upload directory is even created — confirmed via a test that
  checks zero filesystem footprint on rejection.
- **Flat storage directory layout** (`<upload_dir>/<uuid>.<ext>`, no
  per-user subdirectories) — this was the "Checkpoint 2 decision" the
  schema comments flagged as not-yet-made; now made. UUID filenames
  are already globally collision-proof, so nesting would add
  complexity without solving a real problem yet.
- `storage_path` records the actual path used at write time (not
  recomputed from `stored_filename` + current config on each use) —
  protects against a future `UPLOAD_DIR` config change breaking
  resolution of files written under the old path.
- `delete_file()` is idempotent — a missing file is treated as
  already-deleted, not an error, so a caller retrying a cleanup
  doesn't need to special-case "already gone." Genuine filesystem
  failures still raise `StorageError`.
- Added `upload_dir` and `allowed_upload_extensions` (+
  `allowed_upload_extensions_list` property) to `app/core/config.py`,
  matching the existing `cors_origins`/`cors_origins_list` pattern.
- Added `tests/test_storage_service.py` — 18 tests (extension
  validation, stored-filename uniqueness, directory auto-creation,
  save/read-back round-trip, no-collision on duplicate original
  filenames, rejection leaves no footprint, filesystem-error wrapping
  for both save and delete, delete idempotency). 40/40 backend tests
  passing overall (22 pre-existing + 18 new), zero regressions.
- Verified against the real filesystem (not just pytest's `tmp_path`
  sandbox): saved and deleted real files under the real default
  `storage/uploads` directory, confirmed automatic directory creation,
  confirmed no collision between two uploads sharing an original
  filename, confirmed idempotent delete, cleaned up afterward.
- Scope strictly limited to storage per this checkpoint — no DB
  interaction (no `document_service.py` yet), no upload endpoint, no
  API route, no frontend change.
