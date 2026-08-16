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

### Task 3B — Checkpoint 3: Document upload API
- Added `app/api/documents.py`: `POST /documents/upload` —
  `get_current_user`'s first real route consumer. Reads the upload,
  enforces the size cap, delegates to `document_service`, translates
  its domain exceptions to HTTP responses.
- Added `app/services/document_service.py`: `create_document()` — the
  first place `storage_service` (Checkpoint 2) and `Document`
  (Checkpoint 1) are composed together.
- Added `app/schemas/document.py`: `DocumentResponse` — deliberately
  excludes `stored_filename`/`storage_path` (internal storage
  details), mirroring `UserPublic` never exposing `hashed_password`.
- Added `max_upload_size_mb` to `app/core/config.py` (default 20),
  enforced in the router before `document_service`/`storage_service`
  is ever called — fulfills the deferral documented back in
  Checkpoint 2 (size enforcement belongs at the endpoint layer, not
  the storage layer). A rejected oversized upload leaves zero
  filesystem footprint, same principle as a rejected extension.
- Added `python-multipart` dependency — required by FastAPI's
  `UploadFile`/multipart form parsing; the app would fail at request
  time without it.
- Status code conventions established for document endpoints: `422`
  for disallowed extension, `413` for oversized file, `500` for
  genuine storage failures.
- `content_type` is trusted from the client's declared value, not
  independently verified — consistent with the already-documented
  decision to skip `python-magic` content-sniffing for Phase 1.
- Added `tests/test_documents_api.py` — 10 HTTP-level tests: auth
  required, invalid token rejected, PDF/DOCX/TXT upload success,
  response doesn't leak internal fields, file actually written to
  disk, disallowed extension rejected, oversized file rejected (zero
  footprint), two users upload independently with distinct IDs.
  50/50 backend tests passing overall (40 pre-existing + 10 new),
  zero regressions.
- Verified against a real running backend + real Postgres (not just
  pytest): real signup/login, real multipart upload via curl, real
  file confirmed on disk, real `documents` row confirmed via direct
  `psql` query with all fields correctly populated, 401 without a
  token, 422 for a disallowed extension, mismatched declared
  Content-Type vs. extension confirmed to be accepted (extension
  governs, as designed).
- No listing/detail/download/delete endpoints — upload only, per this
  checkpoint's scope. Code committed but not yet pushed as of this
  documentation sync.

### Task 3B — Checkpoint 4: Housekeeping (final checkpoint of Task 3B)
- `backend/.env.example`: documented the storage configuration that's
  been live in code since Checkpoints 2–3 but never added here —
  `UPLOAD_DIR`, `ALLOWED_UPLOAD_EXTENSIONS`, `MAX_UPLOAD_SIZE_MB`. No
  speculative variables added; all three already have real defaults
  and real effects in the running app.
- `.gitignore`: added `backend/storage/uploads/` — uploaded files are
  user content and must never be tracked in git.
- `docker-compose.yml`: added a named volume (`uploads_data`) mounted
  at `/app/storage/uploads` (the container path corresponding to
  `UPLOAD_DIR`'s default), nested under the existing `./backend:/app`
  bind mount. Uploaded files now persist in a real Docker volume
  (survives container recreation, doesn't get dumped into the host's
  git-tracked `backend/` folder) while the rest of the app code still
  hot-reloads via the bind mount, as before.
- No application code changed — `storage_service.py`, `Document`,
  the upload endpoint, and authentication are all untouched.
- `docs/API.md` reviewed against the real routes: already accurate
  (confirmed via direct diff against the previously-synced version) —
  correctly documents only `POST /documents/upload` as implemented,
  correctly notes list/detail/download/delete are not. No changes
  needed.
- Verified: full backend test suite still passes (50/50 — no test
  count change, since this checkpoint touches no application code); `.env.example` values match `config.py`'s real
  defaults; `.gitignore` pattern confirmed against a real test file
  written to `backend/storage/uploads/` and cleaned up afterward;
  `docker-compose.yml`'s mount path confirmed against the Dockerfile's
  actual `WORKDIR`.
- This completes all four planned Task 3B checkpoints (data layer,
  storage service, upload API, housekeeping). Document list, detail,
  download, and delete endpoints remain unimplemented and are not
  part of this checkpoint's — or the originally-approved Task 3B
  checkpoint plan's — scope; see `docs/PROJECT_CONTEXT.md` for what's
  next.

### Document Management CRUD — Checkpoint 1: List documents
- Added `GET /api/v1/documents` — authenticated listing of the
  current user's documents, newest first, bounded pagination
  (`skip`/`limit` query params, `limit` capped 1–100, default 20).
- Extended `app/services/document_service.py` with
  `list_documents_for_user()` rather than creating a parallel
  listing service — the existing service already owned document
  persistence; listing is a natural extension of the same boundary.
  Ownership isolation is enforced at the query level
  (`WHERE user_id = :current_user`), not left to the router or the
  caller to remember.
- No model or schema changes: `Document` already had `created_at`
  (via `BaseModel`) and `DocumentResponse` already returned it —
  both reused as-is for ordering and the list response shape.
- `get_current_user` reused unmodified as the route's auth dependency
  — no second authentication mechanism introduced.
- Added 12 new tests to `tests/test_documents_api.py` (reusing its
  existing `client`/`_auth_headers`/`_isolated_upload_dir` fixtures,
  no new test infrastructure): auth required, empty list for a new
  user, own documents returned with correct fields, response doesn't
  leak `stored_filename`/`storage_path`/`user_id`, cross-user
  isolation (user B never sees user A's documents), deterministic
  newest-first ordering (verified with a real time gap between
  inserts, same technique as Task 2.1's `updated_at` ordering tests),
  `limit` pagination, `skip` pagination, `limit` upper/lower bound
  rejection, negative `skip` rejection, default pagination.
  62/62 backend tests passing overall (50 pre-existing + 12 new),
  zero regressions.
- Verified against a real running backend (not just pytest): two
  real users, two real uploads, listed via real `curl` requests —
  confirmed newest-first ordering by real timestamps, confirmed user
  B's empty list, confirmed `401` with no token, confirmed `422` for
  `limit=0`, confirmed the upload endpoint still works unmodified
  (regression check), confirmed `GET /api/v1/documents` is registered
  at exactly that path (no trailing slash, no redirect) via the
  live OpenAPI schema.
- Detail, download, and delete endpoints remain unimplemented — this
  checkpoint is listing only, per its approved scope.

### Document Management CRUD — Checkpoint 2: Document detail
- Added `GET /api/v1/documents/{document_id}` — authenticated,
  ownership-enforced single-document metadata lookup.
- Extended `app/services/document_service.py` with
  `get_document_for_user()` (third function, alongside
  `create_document` and `list_documents_for_user` — same one-service-
  per-resource pattern, not a parallel service).
- **Ownership enforced via a single combined `WHERE id = :id AND
  user_id = :user_id` clause**, not "fetch by id, then check
  ownership in Python." This means there is no code path where the
  service ever loads another user's row into memory, and — as a
  structural consequence, not a separate check — a wrong-owner
  request and a nonexistent-ID request are indistinguishable at the
  query level. The router turns a `None` result into a single,
  uniform `404 Document not found` for both cases.
- No model or schema changes: `Document.id` (UUID, via `BaseModel`)
  and `DocumentResponse` were both already exactly what the endpoint
  needed.
- Added 8 new tests to `tests/test_documents_api.py`: owner retrieves
  their own document, unauthenticated request rejected, nonexistent
  ID returns 404, cross-user request returns 404, wrong-owner and
  nonexistent-ID responses are byte-identical (not just same status
  code — same body), response doesn't leak `stored_filename`/
  `storage_path`/`user_id`, malformed (non-UUID) ID rejected with
  422, and an explicit regression check that upload and list both
  still work after the new route was added. 70/70 backend tests
  passing overall (62 pre-existing + 8 new), zero regressions.
- Verified against a real running backend with two real users: real
  upload, real retrieval of the owner's own document with correct
  fields, real cross-user 404, real nonexistent-ID 404 with an
  identical response body, real 401 with no token, confirmed via the
  live OpenAPI schema that `/documents/upload`, `/documents`
  (list), and `/documents/{document_id}` are all registered as
  distinct routes with no path-matching conflicts (the static
  `/upload` path is correctly not swallowed by the dynamic
  `{document_id}` segment).
- Download and delete endpoints remain unimplemented — this
  checkpoint is detail-lookup only, per its approved scope.

### Document Management CRUD — Checkpoint 3: Document download
- Added `GET /api/v1/documents/{document_id}/file` — authenticated,
  ownership-enforced download of the actual stored file.
- Extended `app/services/storage_service.py` with the smallest
  appropriate read-side addition: `get_file_path()` (verifies the
  file still exists on disk, returns a `Path`) and a new
  `StoredFileNotFoundError` domain exception for the case where a
  `Document` row survives but its file doesn't. No redesign — save
  and delete are untouched.
- Extended `app/services/document_service.py` with
  `get_document_file_for_user()`, which **reuses**
  `get_document_for_user()` for the existence+ownership check (no
  duplicated `WHERE` clause) and then composes `storage_service`,
  matching the same composition pattern `create_document` already
  established in Task 3B Checkpoint 3.
- **Router uses FastAPI's `FileResponse`**, which streams from disk
  rather than loading the file into memory, and gets HTTP range-request
  support (`Accept-Ranges: bytes`) for free from Starlette — no custom
  streaming code written, per the explicit instruction to only rely on
  framework behavior that's already there.
- **`Content-Disposition` uses the document's `original_filename`**,
  never the internal UUID-based `stored_filename` — confirmed via a
  real downloaded file's headers, not just read from the code.
- **A missing underlying file is a distinct `500`**, not folded into
  the `404` used for "document not found" — deliberately different
  situations (a server-side data-integrity problem vs. a client
  request for something that was never theirs), and the `500` body
  never includes the real filesystem path.
- Added 9 new tests to `tests/test_documents_api.py`: owner downloads
  their own file with correct bytes/content-type/filename,
  unauthenticated rejected, nonexistent 404, cross-user 404,
  wrong-owner/nonexistent responses byte-identical, malformed UUID
  422, no internal path/filename leakage in response headers, missing-
  underlying-file 500 (simulated by deleting the real file from disk
  after upload, then requesting download), and an explicit
  upload+list+detail regression check. 79/79 backend tests passing
  overall (70 pre-existing + 9 new), zero regressions.
- Verified against a real running backend: real file uploaded, real
  download with byte-for-byte content match (`diff` against the
  original), real headers inspected (`content-type`,
  `content-disposition`, `accept-ranges`), real cross-user 404, real
  malformed-UUID 422, and the missing-file 500 reproduced for real by
  manually deleting the on-disk file and re-requesting it — confirmed
  the response body never contains the real path. Confirmed via the
  live OpenAPI schema that all four document routes
  (`/upload`, `""`, `/{id}`, `/{id}/file`) are registered as distinct
  paths with no conflicts.
- Delete remains unimplemented — this checkpoint is download only,
  per its approved scope.

### Document Management CRUD — Checkpoint 4: Document delete
**This completes the full Document Management CRUD surface: upload, list, detail, download, delete.**
- Added `DELETE /api/v1/documents/{document_id}` — authenticated,
  ownership-enforced deletion of both the stored file and the
  database record. `204 No Content` on success (no existing project
  convention uses a custom JSON success body for an action endpoint).
- Extended `app/services/document_service.py` with
  `delete_document_for_user()`, the fifth function — reuses
  `get_document_for_user()` for the existence+ownership check (no
  duplicated query), reuses `storage_service.delete_file()` as-is (no
  new filesystem helper).
- **Data-integrity decision — deletion ordering (the central design
  question for this checkpoint):** the file is deleted **before** the
  database row, not after. A Postgres row and a disk file cannot be
  removed in one atomic transaction, so one order or the other has to
  be chosen deliberately:
  - **File-first (chosen):** if the file delete succeeds but the DB
    delete/commit then fails, the result is a DB row referencing a
    now-missing file. This is *not a new failure mode* — it's exactly
    what `get_document_file_for_user()` (Checkpoint 3) already handles
    cleanly via a distinct `500`, not a crash. The row stays visible
    and deletable, and retrying `DELETE` succeeds, since
    `delete_file()` is already idempotent (a missing file is treated
    as success).
  - **DB-first (rejected):** if the DB commit succeeds but the
    subsequent file delete fails, the file is orphaned on disk with
    **no DB row left to ever reference it again** — a silent,
    permanent storage leak with no retry path.
  - File-first is strictly safer given `delete_file()`'s existing
    idempotency guarantee, and required no new policy — it directly
    reuses that guarantee rather than inventing one.
- If `delete_file()` raises `StorageError` (a genuine filesystem
  failure, distinct from "already missing"), that exception
  propagates as a `500` and the Document row is deliberately **left
  untouched** — "row still there, file still there" is a safe,
  inspectable state; "row gone, file orphaned" is not.
- **A second `DELETE` of an already-deleted document returns `404`,
  not another `204`** — once deleted, the document genuinely no
  longer exists for this user, so "not found" is the accurate
  response. This was a deliberate choice, not an oversight — some
  REST APIs return idempotent `204`s on repeat delete, but nothing in
  this project's existing conventions requires that, and returning an
  accurate "not found" was judged the more honest response.
- Added 13 new tests to `tests/test_documents_api.py`: owner delete
  success (204, empty body), deleted document 404s on detail, deleted
  document disappears from list, physical file actually removed from
  disk, unauthenticated rejected, nonexistent 404, cross-user
  rejected (and confirmed the document was **not** actually deleted),
  wrong-owner/nonexistent responses byte-identical, malformed UUID
  422, deleting a document whose file was already manually removed
  still deletes the stale row (per `delete_file()`'s existing
  idempotency), deleting one user's document doesn't affect another
  user's documents, repeated delete returns 404 not 204, and an
  explicit upload+list+detail+download regression check. 92/92
  backend tests passing overall (79 pre-existing + 13 new), zero
  regressions.
- Verified against a real running backend: real upload, real delete
  (204), confirmed the physical file actually gone from disk,
  confirmed the DB row gone via a real `404` on detail, confirmed
  gone from list; real cross-user delete attempt rejected with `404`
  and confirmed the document was genuinely untouched afterward; real
  `401` unauthenticated, real `422` malformed UUID; real missing-file
  delete (manually removed the on-disk file, then deleted via the API)
  confirmed still returns `204` and removes the stale row; confirmed
  via the live OpenAPI schema that `GET` and `DELETE` are both
  correctly registered under `/documents/{document_id}` with no
  conflicts.

### Document Text Extraction — Checkpoint 1: Standalone PDF parse service
- Added `app/services/parse_service.py` — `extract_text(file_path: Path) -> str`,
  a PDF text-extraction primitive completely independent of FastAPI,
  HTTP, SQLAlchemy, the `Document` model, and `document_service.py` —
  mirrors `storage_service.py`'s decoupling philosophy exactly.
- **PDF only.** `.docx` and `.txt` — both valid *upload* extensions
  elsewhere in the app — are explicitly rejected here with
  `UnsupportedFormatError`, not silently mishandled. Extraction for
  those formats is an explicit future decision, not assumed.
- **Failure contract, chosen deliberately to avoid conflating three
  different situations:**
  - A valid PDF with no extractable text (e.g. a scanned image with
    no text layer) returns an **empty string** — this is a legitimate
    outcome, not a failure.
  - A file that doesn't exist, is empty, or is corrupted/not a real
    PDF raises `ParseError` — verified directly against PyMuPDF's
    actual behavior (not assumed): `pymupdf.EmptyFileError` is a
    subclass of `pymupdf.FileDataError`, so one `except` clause
    correctly covers both the empty-file and corrupted-file cases.
  - A `.pdf`-extensioned-but-wrong-format file and a `.docx`/`.txt`
    file both raise, but as *different* exceptions
    (`ParseError` vs. `UnsupportedFormatError`) — deliberately not
    conflated, since "wrong extension" and "right extension, bad
    content" are different problems for a caller to handle.
  - No bare `except Exception` — only `pymupdf.FileDataError` is
    caught, matching `storage_service.py`'s existing discipline of
    catching specific, understood exceptions.
- **Text normalization, explicit and minimal:** each page's text is
  stripped of leading/trailing whitespace, then pages are joined with
  a blank line. No collapsing of internal whitespace, no cleaning, no
  reformatting — documented and tested.
- Uses PyMuPDF's `Document` context-manager support (`with pymupdf.open(...)`)
  for reliable handle closing — verified this is actually supported by
  the installed version before relying on it, rather than assumed.
- Added `pymupdf>=1.24.0` to `backend/pyproject.toml`.
- Added `tests/test_parse_service.py` — 10 new tests, all independent
  of `conftest.py`'s DB/HTTP fixtures (proof of the decoupling claim,
  not just an assertion of it): valid single-page extraction,
  multi-page order preservation, blank-page-returns-empty-string (not
  an error), corrupted PDF, empty file, nonexistent file, `.docx`
  rejection, `.txt` rejection, direct callability with just a `Path`,
  and the page-joining normalization behavior. **102 backend tests
  passing overall (92 pre-existing + 10 new), zero regressions** —
  exact count from an actual `pytest` run, not estimated.
- Manually verified by calling `extract_text()` directly (not through
  the API, since the API is intentionally untouched this checkpoint)
  against a real generated multi-page PDF, a real corrupted file, and
  a real `.docx` file — all behaved exactly per the documented
  contract. One incidental observation from manual testing: an em-dash
  inserted via PyMuPDF's own `insert_text()` (used only to generate
  test fixtures) rendered as a middle-dot due to a default-font glyph
  substitution — a quirk of the test-fixture-generation helper, not
  of `extract_text()` itself; real uploaded PDFs carry their own
  embedded fonts.
- **NOT wired into upload, `document_service.py`, or any endpoint.**
  No `extracted_text` column, no migration, no change to
  `DocumentResponse`, no change to `POST /documents/upload`'s
  behavior — all deliberately deferred to a future checkpoint that
  will decide the storage/schema question on its own terms (whether
  extracted text belongs on `Document` directly, a separate table, or
  something else — not decided or assumed here).

### Document Text Extraction — Checkpoint 3: `document_texts` schema
- **Approved design (Checkpoint 2) implemented: a separate table, not
  a column on `Document`.** Added `app/models/document_text.py` —
  `DocumentText(BaseModel)` with `document_id` (FK → `documents.id`,
  `ON DELETE CASCADE`, `UNIQUE`, indexed — one index satisfies both
  the uniqueness and index requirements) and `content` (`TEXT NOT
  NULL`, must allow `""` for a valid-but-textless PDF, per
  `parse_service`'s existing contract).
- **No `relationship()`** on either `Document` or `DocumentText` —
  same reasoning already established for `User`↔`Document`: nothing
  needs ORM navigation yet.
- **No `status`, `error`, `parser_version`, or versioning column** —
  deliberately absent, per the approved design. Presence/absence of a
  `document_texts` row is the only (implicit, minimal) signal
  available at this stage; a real status mechanism is explicitly
  future work once processing becomes asynchronous.
- Registered in `app/models/__init__.py`, same pattern as every other
  model.
- Generated migration `f5a18872f21b_create_document_texts_table` via
  `alembic revision --autogenerate`, hand-reviewed before applying —
  autogenerate detected **only** the intended table and its unique
  index, no unrelated changes. Verified against the real dev
  database: `upgrade head` applies cleanly, `\d document_texts`
  matches the approved schema exactly, `downgrade -1` removes
  *only* `document_texts` (confirmed `users`/`documents` untouched),
  `upgrade head` reapplies cleanly.
- Added `tests/test_document_text_model.py` — 6 new tests: insertion
  and retrieval, empty content persists successfully (not conflated
  with failure), duplicate `document_id` rejected by the real
  Postgres unique constraint, cascade delete when the parent
  `Document` is deleted, a `DocumentText` referencing a nonexistent
  `Document` is rejected by the real FK constraint (this project's
  tests run against real Postgres, not SQLite, so this FK is
  genuinely enforced, not merely assumed), and realistic multi-line
  content (including embedded newlines) persists unchanged. **108
  backend tests passing overall (102 pre-existing + 6 new), zero
  regressions.**
- Manually verified against the real dev database (not just the test
  DB): real insertion with embedded-newline content preserved exactly,
  real duplicate-`document_id` rejection, real empty-content
  persistence (re-fetched fresh to confirm), real cascade delete.
- **No dependency change** — no new package needed for a schema-only
  checkpoint.
- **Confirmed untouched, by direct `grep` after implementation, not
  just by not having edited them:** `parse_service.py`,
  `document_service.py`, `app/api/documents.py`, `app/models/document.py`,
  `app/schemas/document.py`, `storage_service.py`. No upload wiring,
  no parsing orchestration, no chunking, no embeddings, no status
  tracking, no versioning, no background processing, no API endpoint
  for extracted text.

### Document Text Extraction — Checkpoint 4: Parse -> persist integration
- Added `app/services/document_text_service.py` —
  `parse_and_store_document_text(db, *, document)`, the first place
  `parse_service` (Checkpoint 1) and `document_texts` (Checkpoint 3)
  are actually connected. A new dedicated service file, not a 6th
  function on `document_service.py` — mirrors the same
  Document/DocumentText separation already made at the DB layer
  (Checkpoint 2) one layer up: `document_service` owns Document
  metadata operations; `document_text_service` owns the derived-text
  parse-and-persist operation, composing `storage_service` and
  `parse_service` the same way `document_service` already composes
  `storage_service`.
- **Accepts an already-authorized `Document` object, not a
  `document_id`.** No ownership check is performed or duplicated here
  — the caller (a future upload-wiring or reprocess endpoint, neither
  of which exists yet) is responsible for having obtained the
  `Document` through the existing authorized path
  (`document_service.get_document_for_user` or the object
  `create_document` already returns).
- **Upsert, not insert-only:** a document with no existing
  `DocumentText` gets one inserted; a document with one already gets
  its `content` updated in place — never a duplicate row (the
  `UNIQUE` constraint on `document_id` guarantees this can't silently
  happen). `created_at` naturally preserves the first-parsed time;
  `updated_at`'s existing auto-refresh naturally captures the
  last-reparsed time — no new column needed for that signal.
  Deliberately not versioned, matching the approved Checkpoint 2/3
  design.
- **Parsing happens strictly before any `document_texts` read or
  write.** A parse failure (`ParseError`, `UnsupportedFormatError`,
  or `storage_service.StoredFileNotFoundError`) propagates unmodified
  and the table is never touched — a consequence of call ordering,
  not a try/except/rollback.
- **Empty extracted text (`""`) is persisted as a normal, successful
  result**, with no special-casing anywhere in the function.
- **Commits internally**, matching the transaction convention already
  established by every mutating `document_service` function.
- **Not wired into upload or any endpoint.** No new API route, no
  `DocumentResponse` change, no `Document` schema change, no new
  migration (`alembic check` confirmed "No new upgrade operations
  detected" after implementation).
- Added `tests/test_document_text_service.py` — 8 new tests: single-
  page parse+persist, multi-page order preservation, blank-PDF
  persists `""`, corrupted PDF raises `ParseError` with zero
  persistence, missing file raises `StoredFileNotFoundError` with
  zero persistence, `.docx` raises `UnsupportedFormatError` with zero
  persistence, reprocessing updates the existing row (same id,
  `created_at` preserved, `updated_at` advanced, still exactly one
  row), and a **real** (not simulated) persistence-failure test — the
  parent `Document` is deleted out from under an already-fetched
  reference before the write, so the `DocumentText` insert
  legitimately violates the real FK constraint, raising
  `IntegrityError`. Test setup uses `document_service.create_document()`
  itself (real file on disk, real `storage_path`) rather than
  hand-constructing `Document` rows, so `document.storage_path` is
  exactly what a real upload would have produced. **116 backend tests
  passing overall (108 pre-existing + 8 new), zero regressions.**
- Verified against the real dev database and real files (not just
  the test DB): a real successful parse+persist, a real empty-PDF
  persist (confirmed the row exists with `content == ""`), and a real
  corrupted-PDF failure (confirmed `ParseError` raised and confirmed
  via direct query that no `DocumentText` row was created). All test
  data cleaned up afterward, confirmed via row counts returning `0`
  on all three tables.
- Chunking, embeddings, RAG, background workers, upload wiring, and
  any API endpoint for extracted text remain entirely unimplemented —
  this checkpoint is the service-level bridge only.

### Document Text Extraction — Checkpoint 5: Explicit processing endpoint
- Added `POST /api/v1/documents/{document_id}/process` to
  `app/api/documents.py` — the first real, authenticated caller of
  `document_text_service.parse_and_store_document_text()` outside of
  tests.
- Preceded by two dedicated design-review turns before any code was
  written: one comparing synchronous upload-wiring against a separate
  explicit endpoint across ten evaluation dimensions, and a second
  resolving the endpoint's exact response contract against four
  concrete alternatives. Both were explicitly approved before
  implementation began.
- **Explicit endpoint chosen over automatic upload wiring.** A parse
  failure is now fully isolated to its own request/response and can
  never affect `/upload`'s success/failure semantics — sidestepping a
  "should upload fail if parsing fails?" policy question that would
  otherwise have required new compensating-transaction/rollback logic
  this codebase doesn't have anywhere else. `/upload` and
  `create_document()` were not modified in any way.
- **The same endpoint also serves as the reprocess mechanism**, for
  free — no separate reprocess endpoint or duplicate-prevention logic
  was added; `parse_and_store_document_text()`'s existing upsert
  behavior (Checkpoint 4) already makes calling `/process` again on
  the same document safe (same row, `content` updated in place, no
  duplicate).
- **Ownership check reused exactly as-is** via
  `document_service.get_document_for_user()` — no new authorization
  helper, no duplicated WHERE clause. A nonexistent document and one
  owned by another user both return an identical `404`.
- **Success response is the existing `DocumentResponse`, at `200`** —
  never `DocumentText` or its `content`. Extracted text remains
  internal processing state, not exposed by this or any endpoint.
- **Error mapping mirrors the router's existing exception-translation
  style:** `UnsupportedFormatError`/`ParseError` → `422`;
  `StoredFileNotFoundError` → `500` (mirrors download's identical
  handling of the same underlying condition). No broad
  `except Exception` anywhere.
- Added `tests/test_document_process_api.py` — 11 new tests: success
  (asserts response shape and that a real `DocumentText` row was
  persisted with the correct content), reprocessing (calls `/process`
  twice, asserts exactly one row exists and is updated in place, not
  duplicated), nonexistent document (`404`), another user's document
  (`404`, indistinguishable from nonexistent, confirms nothing was
  processed), unsupported format (`.docx` upload, `422`, confirms no
  row created), corrupted PDF (`422`, confirms no row created),
  missing stored file (deleted from disk mid-test, `500`, confirms no
  row created), authentication required (no token and invalid token,
  both `401`), invalid UUID path parameter (`422`), and response-shape
  verification that the body never contains `content`,
  `extracted_text`, `storage_path`, or `stored_filename`. **127
  backend tests passing overall (116 pre-existing + 11 new), zero
  regressions** — the full suite was run against a real, freshly
  provisioned PostgreSQL 16 instance both before and after this
  checkpoint's changes.
- Verified beyond pytest: `alembic current`/`heads`/`check` confirmed
  the migration head is unchanged and no new migration was generated;
  `git diff --stat`/`--name-only`/`--check` confirmed the change
  surface is exactly `app/api/documents.py` (modified) plus
  `tests/test_document_process_api.py` (new), with no whitespace
  errors; explicit `git diff --quiet` checks confirmed
  `document_service.py`, `document_text_service.py`,
  `parse_service.py`, `storage_service.py`, `document.py`,
  `document_text.py`, `schemas/document.py`, all three Alembic
  migration files, and everything under `frontend/` are byte-for-byte
  unchanged.
- Chunking, embeddings, pgvector, RAG, chat, background workers,
  Redis/Arq, OCR, any status/processing-state column, parser
  versioning, a `GET` endpoint for extracted text, frontend work, and
  deployment all remain entirely unimplemented — this checkpoint adds
  one endpoint and nothing else.

### Task 3C — Frontend document management
- Preceded by a dedicated scope/design-review turn (before any code
  was written) that inspected the actual frontend and backend
  contract directly rather than from memory — and surfaced that
  `services/api.ts` had no authenticated-request capability at all,
  no route guard existed anywhere, and `components/` was an empty
  placeholder directory. The six-part scope (list, upload, download,
  delete, process, route guard) and two open policy questions (401
  handling, list-refresh strategy) were confirmed before
  implementation began.
- Added `frontend/src/components/ProtectedRoute.tsx` — the project's
  first real route guard. Redirects unauthenticated visitors to
  `/login` (with `replace`, so the protected route doesn't linger in
  browser history). `/documents` is wrapped in it in `App.tsx`.
- Added `frontend/src/pages/DocumentsPage.tsx` — the full document-
  management UI: paginated list (`skip`/`limit`, "Load more"),
  upload (native file input, `.pdf/.docx/.txt` hint), download
  (triggers a browser download via a Blob + temporary anchor, using
  the document's own `original_filename`), delete (with a native
  `confirm()` step), and an explicit "Process" button per row wired
  to `POST /documents/{document_id}/process`. Loading, empty, and
  per-row error/success states throughout. No document detail page
  (the four `DocumentResponse` fields already fit in a list row) and
  no persisted "processed" status badge (the backend has no such
  field) — both deliberate, not oversights.
- Added `frontend/src/services/document.ts` and
  `frontend/src/types/document.ts` — mirrors the existing
  `services/auth.ts`/`types/auth.ts` convention exactly;
  `DocumentResponse` matches `app/schemas/document.py` field-for-field.
- Extended `frontend/src/services/api.ts` with the project's first
  authenticated request helpers: `getAuth`, `postAuth`, `uploadAuth`
  (multipart, deliberately omits `Content-Type` so the browser sets
  the multipart boundary), `deleteAuth`, and `downloadAuth` (returns
  a `Blob`). Added an `ApiError` class (extends `Error`, carries the
  HTTP `status`) so callers can detect `401` without parsing the
  error message string — existing `err instanceof Error` checks in
  `LoginPage`/`SignupPage` keep working unchanged.
- **Policy decisions made explicitly, not defaulted silently:** a
  `401` from any document call logs the user out and redirects to
  `/login` (the frontend has no token-refresh mechanism, so a `401`
  here can only mean an expired/invalid token); the document list is
  refetched from scratch after every mutation rather than updated
  optimistically, matching this project's existing "simplest option
  that works" convention.
- Updated `frontend/src/App.tsx` (registers `/documents`, adds a
  "View my documents" link from the home placeholder for
  authenticated users) and `frontend/src/constants/routes.ts` (adds
  `documents: "/documents"`).
- **No backend file modified.** No new backend route, schema, or
  migration — every frontend call matches an endpoint that already
  existed. Confirmed via `git diff --quiet` against every file under
  `backend/`.
- **No new dependency, frontend or backend.** No HTTP client library,
  form library, pagination library, or CSS framework was introduced
  — everything extends the existing hand-rolled `fetch` wrapper,
  `react-router-dom` primitives, and inline-styled component pattern.
- Verified: `npm run build` (`tsc -b && vite build`) clean, zero
  TypeScript errors; `npm run lint` (oxlint) — 0 warnings, 0 errors
  across all 15 frontend source files; full backend suite re-run,
  **127 passed, unchanged** (confirms zero backend regression).
- **Verified end-to-end against the actual running FastAPI server**
  (not mocked): signup → login → list documents (empty) → upload a
  real PDF (multipart) → list documents (populated) → process (200,
  `DocumentResponse`) → download (200, confirmed the downloaded bytes
  are a real, valid PDF via the `file` command) → delete (204) →
  unauthenticated request (401) → invalid token (401). Every request
  shape the new frontend code sends was exercised directly against
  real backend responses.
- Document detail page, PDF viewer/annotation, chat, any research-
  workspace UI, and any backend change of any kind remain out of
  scope and unimplemented — this task is document management only.

### Document Chunking
- Extends `POST /api/v1/documents/{document_id}/process` to also
  split newly-extracted text into deterministic, ordered chunks and
  persist them (`document_chunks` table), atomically alongside the
  extracted-text write. Preceded by two design-review turns: an
  initial full design, and a corrective second pass (after review)
  that fixed a real transaction-consistency gap in the first proposal
  and fully specified the previously-underspecified chunking-algorithm
  boundary behavior. Both approved before implementation began.
- Added `backend/app/models/document_chunk.py` — `DocumentChunk`
  model, FK'd to `document_texts` (not `documents`), composite
  `UniqueConstraint(document_text_id, chunk_index)`, no
  `relationship()`, no embedding/vector column.
- Added migration `411324fb18f5_create_document_chunks_table` —
  verified against the actual repository's `alembic heads`/`current`
  (both `f5a18872f21b (head)`) before generating, autogenerated,
  hand-reviewed (detected only the intended table + index, no
  drift), applied. `alembic current`/`heads` now
  `411324fb18f5 (head)`; `alembic check` — "No new upgrade operations
  detected."
- Added `backend/app/services/chunk_service.py` — `chunk_text()`, a
  pure, fully deterministic function (`TARGET=1000`, `MAX=1200`,
  `OVERLAP=150`, `WHITESPACE_LOOKBACK=20` characters; paragraphs
  split on `"\n\n"`, greedily combined up to `TARGET`, a paragraph
  between `TARGET` and `MAX` kept whole, a paragraph over `MAX`
  hard-split into overlapping, whitespace-snapped windows). No
  tokenizer or NLP dependency. Exposes only `chunk_text()` (pure) and
  a private, non-committing `_replace_chunks()` — deliberately no
  public self-committing chunking entry point, since no real caller
  needs independent chunk persistence.
- **The critical fix this milestone centers on:** text persistence
  and chunk persistence now happen in **one transaction**, not two
  independent commits. `document_text_service.py` gained a private,
  non-committing `_upsert_document_text()`; the existing public
  `parse_and_store_document_text()` is now a thin wrapper around it
  with unchanged behavior (all 8 of its existing tests pass
  unmodified). Added `backend/app/services/document_processing_service.py`
  — a new orchestration layer whose `process_document()` calls both
  private helpers, then issues the single `db.commit()`. Relies on
  `app/db/session.py`'s existing `get_db` dependency's implicit
  rollback-on-exception; verified directly by a test that forces a
  chunk-persistence failure after text has been staged and confirms
  the prior committed state (old text, old chunks, same row IDs)
  survives completely unchanged.
- Updated `backend/app/api/documents.py` — `/process` now calls
  `document_processing_service.process_document()` instead of
  `document_text_service` directly. Request/response contract
  unchanged; error mapping unchanged.
- Reprocessing replaces chunks via delete-then-recreate (not
  update-in-place, not versioned) — chunk count changes with content,
  so there's no stable row correspondence to update against, unlike
  `DocumentText`'s single-row upsert. Verified to leave zero stale
  rows and zero duplicates after a reprocess with materially
  different content.
- Added `backend/tests/test_chunk_service.py` (14 tests — pure
  algorithm: empty/whitespace-only input, short-paragraph combining,
  multi-chunk splitting, paragraph-between-target-and-max staying
  whole, oversized-paragraph hard-splitting with overlap, whitespace
  snap-back, raw-cut fallback with no whitespace; plus DB-level
  `_replace_chunks()` tests: persistence, sequential `chunk_index`,
  empty-text-zero-chunks, reprocessing replaces without duplicates).
- Added `backend/tests/test_document_chunk_model.py` (7 tests —
  schema-level: insert/retrieve, many-chunks-per-text, composite
  uniqueness rejection, cross-document-text uniqueness allowed,
  cascade delete from `DocumentText`, full cascade chain from
  `Document`, FK-integrity rejection).
- Added `backend/tests/test_document_processing_service.py` (5 tests
  — the core atomicity coverage: successful joint persistence, parse
  failure persists nothing, reprocessing replaces both text and
  chunks, **the critical chunk-failure-after-text-staged case
  proving rollback leaves prior state intact**, empty text persists
  zero chunks).
- Extended `backend/tests/test_document_process_api.py` (6 existing
  tests extended, no new test functions needed) — success and
  reprocessing tests now also assert chunk persistence/replacement;
  the four failure-case tests (unsupported format, corrupted PDF,
  missing file, wrong owner) now also assert zero `DocumentChunk`
  rows created; the response-shape test now also asserts `"chunks"`
  is never in the body.
- **153 backend tests passing overall (127 pre-existing + 26 new),
  zero regressions** — verified by actually running the full suite
  against real Postgres, including all 8 pre-existing
  `test_document_text_service.py` tests confirming the
  `_upsert_document_text()` extraction didn't change that module's
  public behavior.
- No new dependency — no tokenizer, no NLP library, nothing beyond
  what was already installed.
- No frontend change of any kind — chunks are internal processing
  state with no UI surface, same treatment extracted text already
  had.
- No new endpoint — chunking was integrated into the existing
  `/process` endpoint rather than exposed separately, specifically to
  avoid a client being able to call one without the other and end up
  with mismatched text/chunk freshness.
- Embeddings, pgvector, vector storage, semantic retrieval, RAG,
  single-document chat, background workers, Redis/Arq, OCR, any
  status/versioning column on chunks, and any new public endpoint all
  remain entirely unimplemented — this milestone adds deterministic
  chunk generation and atomic persistence only.

### Document Chunks → Embeddings
- Extends `POST /api/v1/documents/{document_id}/process` to generate
  and persist an OpenAI `text-embedding-3-small` embedding (1536
  dimensions) for every chunk, atomically alongside the extracted
  text and chunks. Preceded by a full design review and a corrective
  design-resolution pass (OpenAI pricing/docs verified directly,
  official SDK chosen over hand-rolled `httpx`, and — critically — a
  processing-order fix required by `DocumentChunk.embedding` being
  `NOT NULL`), both approved before implementation began.
- Added `backend/app/services/embedding_service.py` —
  `embed_texts(texts: list[str]) -> list[list[float]]`, the only
  place the OpenAI SDK is instantiated or called anywhere in this
  codebase. Batches an entire document's chunks into one API request;
  sorts the response by its own `index` field (not array order);
  validates the returned count matches the input; translates every
  provider/network/timeout/malformed-response failure into one
  domain exception, `EmbeddingProviderError` — never a raw `openai`
  exception, never a leaked API key or response body. No provider
  interface, no strategy pattern, no registry — exactly one provider,
  exactly one real caller.
- Added `backend/alembic/versions/368647c4431f_add_embedding_column_to_document_chunks.py`
  — adds `document_chunks.embedding` as `vector(1536) NOT NULL`.
  Verified the real migration head (`411324fb18f5`) before generating.
  The autogenerated migration was hand-reviewed before applying, and
  that review caught a real bug: the missing `import pgvector.sqlalchemy`
  line (would have raised `NameError` at runtime) was added by hand.
  The migration also now runs `CREATE EXTENSION IF NOT EXISTS vector`
  as its first statement — required once per database in any
  environment, not just this sandbox, since the extension binary
  being present (via the `pgvector/pgvector:pg16` Docker image) is
  not the same as the extension being enabled inside a given database.
- **`DocumentChunk.embedding` is `NOT NULL`, not nullable** — the
  milestone's central invariant is that a committed chunk should
  never exist without its embedding, and `NOT NULL` lets Postgres
  itself enforce that rather than relying on application discipline.
  This required restructuring the processing order: `chunk_service.
  _replace_chunks()` no longer computes chunk text internally — it
  now accepts pre-computed `chunk_texts` and `embeddings` and
  constructs every `DocumentChunk` with both already set, before any
  flush. `chunk_text()` itself (the deterministic algorithm) is
  completely unchanged.
- `document_processing_service.process_document()` extended to the
  full flow: parse → upsert `DocumentText` → `chunk_text()` →
  `embedding_service.embed_texts()` (skipped entirely for zero
  chunks — no embedding request is ever made for empty extracted
  text) → `_replace_chunks()` (with embeddings already attached) →
  one `db.commit()`. Because `_replace_chunks()` (which deletes the
  prior chunk set) is only called after `embed_texts()` has already
  succeeded, an embedding-provider failure during reprocessing can
  never destroy the previous, still-valid chunks — the delete step is
  structurally unreachable until new embeddings are already in hand.
- Added `EmbeddingProviderError → 502 Bad Gateway` to
  `backend/app/api/documents.py`'s existing exception-translation
  chain — a genuinely new failure category this API introduces
  (distinct from `422`, the client's document content, and `500`,
  this project's own storage integrity: `502` is specifically "an
  upstream dependency we depend on failed"). Generic client-facing
  message only; no raw provider error text, response body, or API
  key ever reaches the response.
- Added `openai_api_key` (no default — every real environment must
  set it), `embedding_model` (default `"text-embedding-3-small"`),
  and `embedding_dimensions` (default `1536`) to
  `backend/app/core/config.py`, following the existing
  `pydantic-settings` pattern exactly. Corresponding entries added to
  `backend/.env.example`.
- Added `openai` and `pgvector` to `backend/pyproject.toml` — the
  only two new runtime dependencies this milestone required. No
  tokenizer, no NLP library, no vector-database client.
- Added `backend/tests/test_embedding_service.py` — 8 new tests
  (successful generation, order preservation despite a shuffled
  provider response, mismatched-count rejection, API-error wrapping,
  timeout-error wrapping, connection-error wrapping, malformed-
  response handling, empty-input-makes-no-call). No real OpenAI API
  call is ever made — every test monkeypatches the client boundary.
- Extended `backend/tests/test_document_chunk_model.py` (+2 tests:
  embedding storage/retrieval with dimensionality check, `NOT NULL`
  enforcement against real Postgres) and `backend/tests/test_chunk_service.py`
  (+3 tests: each chunk receives its corresponding embedding,
  mismatched chunk/embedding counts rejected, `_replace_chunks()`
  never commits independently) — the existing pure-algorithm tests in
  the latter file are entirely unchanged.
- Extended `backend/tests/test_document_processing_service.py` (+2
  tests: embedding failure on first processing persists nothing, and
  — the most important test of this milestone — embedding failure
  during reprocessing leaves the previously committed `DocumentText`,
  chunks, and embeddings completely unchanged, verified by row ID and
  embedding-value equality, not just row count) and
  `backend/tests/test_document_process_api.py` (+1 test: embedding
  failure returns `502` with no provider-detail leakage; existing
  tests extended to assert embeddings are present and `"embedding"`
  is never in the response body).
- **A real gap discovered in the test suite's own `client` fixture
  while writing the `502` test:** `tests/conftest.py`'s
  `_override_get_db()` shares one session across an entire test with
  no `async with`/`finally`, unlike production's real `get_db()` —
  meaning it doesn't automatically roll back on a mid-request
  exception the way `AsyncSession.close()` does in production. The
  new `502` test calls `db_session.rollback()` explicitly to
  reproduce that same guarantee, mirroring the technique the
  Document Chunking milestone's own atomicity tests already
  established.
- **169 backend tests passing overall (153 pre-existing + 16 new),
  zero regressions** — verified by actually running the full suite
  against real Postgres, not assumed.
- Verified beyond pytest: `alembic heads`/`alembic current` checked
  against the real repository *before* generating the migration
  (both `411324fb18f5 (head)`, matching the expected pre-milestone
  state); after applying, `alembic current`/`heads` both
  `368647c4431f (head)`, `alembic check` → "No new upgrade operations
  detected"; `git status --short`/`git diff --stat`/`--name-only`
  confirmed the exact, minimal change surface matching the approved
  file list, with no unrelated file touched.
- No frontend change of any kind. No new public endpoint — embedding
  generation was integrated into the existing `/process` endpoint,
  not exposed separately, to avoid a client calling one operation
  without the other and ending up with mismatched text/chunk/
  embedding freshness. Vector index, semantic search, retrieval, RAG,
  chat, background workers, Redis/Arq, embedding metadata/versioning
  columns, automatic retries, and a provider abstraction layer all
  remain entirely unimplemented — this milestone adds deterministic
  embedding generation and atomic persistence only.

### Vector Retrieval
- Added `backend/app/services/retrieval_service.py` —
  `retrieve_similar_chunks(db, *, document, query_embedding, top_k)`,
  the first real consumer of `document_chunks.embedding`. Performs a
  pgvector cosine-distance similarity search
  (`DocumentChunk.embedding.cosine_distance(...)`) scoped to a single,
  already-authorized `Document`, joining through `DocumentText` (the
  only path from a `Document` to its chunks, since `DocumentChunk` has
  no direct `document_id`). Returns a small `RetrievedChunk` dataclass
  (`id`, `chunk_index`, `content`, `distance`) — no Pydantic schema,
  since nothing here crosses an HTTP boundary.
- **Internal-only — no new public endpoint.** `retrieval_service` is a
  service-layer primitive with no current HTTP caller, ready for a
  future RAG/chat layer to consume; matches this project's standing
  "no speculative public API" principle.
- **Zero authorization logic of its own.** Accepts an already-
  authorized `Document`, mirroring the exact pattern already
  established by `document_text_service.parse_and_store_document_text()`
  — the caller is responsible for obtaining the `Document` via
  `document_service.get_document_for_user()` first. Document isolation
  is enforced structurally by the join/filter, not by an ownership
  check inside this module.
- Extended `backend/app/services/embedding_service.py` with
  `embed_query(text: str) -> list[float]` — a thin wrapper around the
  existing `embed_texts()` (a query is just one text; no second call
  path to the provider, no duplicated batching/error-translation
  logic). `embed_texts()` itself is unchanged.
- `top_k` is clamped to `[1, MAX_TOP_K=20]` rather than trusted as-is
  — a caller-supplied `0`, negative, or unreasonably large value is
  silently corrected rather than raising, since this is an internal
  primitive with no request-validation boundary yet.
- **No new migration, no vector index.** `document_chunks.embedding`
  already exists (from the prior milestone); Alembic head unchanged
  at `368647c4431f`. An ANN index (HNSW/IVFFlat) solves a Phase 2+
  (cross-document, large-scale) retrieval problem that doesn't exist
  yet at Phase 1's single-document scale — confirmed still correct,
  not revisited.
- Added `backend/tests/test_retrieval_service.py` — 8 new tests, all
  against real Postgres/pgvector with deterministic, hand-constructed
  unit-vector embeddings (never real OpenAI output): similarity
  ordering (closest-first, verified by exact cosine-distance values —
  identical vectors → distance ≈0.0, orthogonal vectors → distance
  ≈1.0), `top_k` respected, pathological `top_k` (zero, huge) clamped
  safely, document isolation (two documents with identical embeddings
  never leak into each other's results), empty result for a document
  with zero chunks, full result-shape verification (id/chunk_index/
  content/distance all correct), and confirmation that retrieval never
  calls `embedding_service` (and therefore never the OpenAI SDK)
  itself.
- Extended `backend/tests/test_embedding_service.py` with 2 new tests
  for `embed_query()` — correct single-text/model/dimensions request,
  and provider-failure translation to the existing
  `EmbeddingProviderError`. No real OpenAI API call, same mocking
  convention as every other test in this file.
- **179 backend tests passing overall (169 pre-existing + 10 new),
  zero regressions.** `alembic current`/`heads` unchanged at
  `368647c4431f (head)`; `alembic check` — "No new upgrade operations
  detected" (confirming no schema drift was introduced).
- No frontend change, no new endpoint, no RAG, no chat, no vector
  index, no Redis/background worker, no provider abstraction —
  all confirmed still out of scope and unimplemented. This milestone
  adds a tested, internal similarity-search building block only.

### RAG Foundation
- Added `backend/app/services/llm_service.py` — `generate_answer
  (system_prompt, user_prompt) -> str`, the only place the OpenAI
  Chat Completions API is called anywhere in this codebase. Mirrors
  `embedding_service.py`'s exact shape: one small module wrapping one
  external capability, no provider interface, no strategy pattern.
  Uses Chat Completions (not the newer Responses API) — the simplest
  fit for a plain system+user → text-answer call with no tool
  calling, agents, multi-turn state, or streaming. Client is
  constructed per call, never at import time, so importing this
  module never requires an API key. Defines `LLMProviderError`,
  wrapping every `openai.OpenAIError` (and a defensive check for a
  malformed/empty response) without leaking raw provider text,
  response bodies, or the API key — the same translation pattern
  `EmbeddingProviderError` already established.
- Added `backend/app/services/rag_service.py` — `answer_question(db,
  *, document, question, top_k=DEFAULT_TOP_K) -> str`, the
  orchestration layer connecting the existing embedding and retrieval
  primitives to the new LLM service:
  `embed_query()` → `retrieve_similar_chunks()` → prompt assembly →
  `llm_service.generate_answer()`. Mirrors the exact composition
  pattern already established by `document_processing_service.py` —
  a thin orchestrator that composes other focused services without
  owning their internals.
- **Stage A only — no HTTP endpoint.** `rag_service` is a pure
  internal service with no current caller beyond its own tests. A
  thin authenticated endpoint is an explicitly separate, future
  decision, not bundled into this milestone.
- **No authorization duplication.** `answer_question()` accepts an
  already-authorized `Document` and performs zero ownership checks of
  its own — the exact same pattern already established by
  `document_text_service` and `retrieval_service`. There is no path
  where a caller can reach another user's document chunks through
  this service.
- **Grounded system prompt.** States that retrieved document text is
  the only source for factual claims, that the model must never
  invent information absent from context, must explicitly say when
  context is insufficient, must treat retrieved text as untrusted
  reference material rather than instructions, must never follow
  instructions embedded inside retrieved text, and must never claim
  to have read unretrieved portions of the document. No separate
  prompt-injection framework — prompt-level instructions only, per
  the approved design.
- **Deterministic context format.** Retrieved chunks are numbered
  (`[Context Chunk 1]`, `[Context Chunk 2]`, ...) in retrieval order
  exactly as returned by `retrieve_similar_chunks()` — no reordering.
  Chunk UUIDs, cosine distances, and embeddings are never sent to the
  LLM; only the chunk text.
- **Deterministic empty-retrieval behavior.** If
  `retrieve_similar_chunks()` returns no chunks, `answer_question()`
  returns a fixed `NO_CONTEXT_ANSWER` string immediately, without
  calling the LLM at all — chosen over sending empty context and
  hoping the model says "I don't know," per the project's stated
  preference for deterministic application behavior.
- **`top_k` is not duplicated.** `rag_service` passes `top_k` straight
  through to `retrieve_similar_chunks()`, reusing
  `retrieval_service.DEFAULT_TOP_K` as its own default — no second
  top-k constant, no duplicated `[1, MAX_TOP_K]` clamping policy.
- Added `llm_model` to `backend/app/core/config.py` (default
  `"gpt-5.4-mini"`, worth re-confirming against current OpenAI
  documentation at deploy time, same discipline already applied to
  `embedding_model`'s own default) — a setting distinct from
  `embedding_model`, sharing the same `openai_api_key`. Corresponding
  entry added to `backend/.env.example`.
- **No database schema change, no migration.** RAG is a stateless
  request/response pipeline over already-existing data; nothing here
  requires persistence. Alembic head unchanged at `368647c4431f`.
- Added `backend/tests/test_llm_service.py` — 10 new tests: correct
  model passed, correct system/user messages passed, generated text
  returned, provider/timeout/connection errors wrapped as
  `LLMProviderError`, no provider details leaked, malformed/empty
  response handled safely, and confirmation the service works with no
  `OPENAI_API_KEY` set anywhere in the environment.
- Added `backend/tests/test_rag_service.py` — 10 new tests, following
  the project's "mock only the external-provider boundary, exercise
  real orchestration otherwise" philosophy: the question is actually
  embedded and actually retrieved against real Postgres/pgvector
  (only the embedding and LLM *clients* are mocked, not the service
  functions themselves) — covering the full flow, document isolation
  (two documents with identical chunk embeddings never cross-
  contaminate an answer), deterministic chunk ordering and numbering
  in the prompt, grounding/untrusted-context instructions present in
  the system prompt, empty retrieval returning the fallback without
  any LLM call, `LLMProviderError` propagation, and `top_k`
  passthrough to retrieval.
- **199 backend tests passing overall (179 pre-existing + 20 new),
  zero regressions.** `alembic current`/`heads` unchanged at
  `368647c4431f (head)`; `alembic check` — "No new upgrade operations
  detected." No real OpenAI API call anywhere in the suite; confirmed
  with `OPENAI_API_KEY` absent from the environment entirely.
- No HTTP endpoint, no chat persistence (`ChatSession`/`ChatMessage`),
  no frontend change, no multi-document/global search, no vector
  index, no Redis/background worker, no provider abstraction, no
  streaming, no tool calling/agents — all confirmed still out of
  scope and unimplemented. This milestone adds the internal RAG
  pipeline only, the foundation single-document chat will eventually
  call.

### Single-Document Chat API
- Added `POST /api/v1/documents/{document_id}/chat` to
  `backend/app/api/documents.py` — the first HTTP-facing surface for
  the RAG pipeline. Authenticated via the existing `get_current_user`
  dependency; ownership resolved via the existing
  `document_service.get_document_for_user()`, reused exactly as-is —
  no duplicated authorization logic, no new ownership helper. A
  nonexistent document and one owned by another user both return an
  identical `404`, matching every other `{document_id}` route.
- Added `backend/app/schemas/chat.py` — `ChatRequest` (`question: str`,
  a Pydantic field validator rejects empty/whitespace-only input at
  the schema boundary, so validation failures return `422` before the
  request ever reaches the router) and `ChatResponse` (`answer: str`
  only — never retrieved chunks, chunk IDs, cosine distances,
  embeddings, prompts, or OpenAI provider metadata; citations/sources
  remain an explicitly deferred, separate future milestone).
- **`rag_service.answer_question()` is treated as a black box** — the
  router performs authentication, ownership resolution, and error
  mapping only. No embedding generation, pgvector query, chunk
  retrieval, prompt construction, or LLM call was moved into (or
  duplicated inside) the router. `rag_service.py`, `llm_service.py`,
  `retrieval_service.py`, and `embedding_service.py` were **not
  modified** — confirmed unnecessary, not just unmodified by
  omission.
- **`LLMProviderError` → `502`**, mirroring the existing
  `EmbeddingProviderError` → `502` precedent already established on
  `/process` — same generic client-facing message, no raw provider
  exception text, response body, or API key ever reaches the
  response. No broad `except Exception` was introduced.
- **Stateless by design** — no conversation ID, no session, no
  history. Each request is independent; a document with no relevant
  chunks still returns `200` with `rag_service`'s existing
  deterministic `NO_CONTEXT_ANSWER` fallback (unchanged behavior,
  simply surfaced over HTTP now).
- **No database migration.** This milestone requires no schema
  change; Alembic head unchanged at `368647c4431f`.
- Added `backend/tests/test_document_chat_api.py` — 11 new tests,
  following `test_document_process_api.py`'s existing conventions
  exactly (isolated upload dir, signup+login for a bearer token, real
  PDF upload via the actual upload endpoint) with one new mocking
  boundary: `rag_service.answer_question()` itself, since this file
  tests the router's orchestration (auth → ownership → RAG call →
  response), not RAG's own internal correctness (already covered by
  `test_rag_service.py`). Covers: successful request returns the
  exact `{"answer": "..."}` shape; missing/invalid authentication
  (`401`); nonexistent document (`404`, RAG never called); another
  user's document (`404`, indistinguishable from nonexistent, RAG
  never called); malformed UUID (`422`); missing/empty/whitespace-only
  question (`422`); the mocked RAG service receives the exact
  authorized `Document`, the exact question text, and a real DB
  session; and `LLMProviderError` → `502` with no provider-detail
  leakage (verified by asserting specific sensitive strings are
  absent from the response body).
- **211 backend tests passing overall (200 pre-existing + 11 new),
  zero regressions.** `alembic current`/`heads` unchanged at
  `368647c4431f (head)`; `alembic check` — "No new upgrade operations
  detected." No real OpenAI API call anywhere in the suite; the RAG
  service boundary is mocked at the exact point the router calls it.
- No chat persistence, no frontend change, no streaming, no
  WebSockets/SSE, no citations/sources schema, no rate limiting, no
  new authentication mechanism, no provider abstraction, no RAG
  Foundation redesign — all confirmed still out of scope and
  unimplemented. This milestone adds one thin, stateless, authenticated
  HTTP endpoint over the existing RAG pipeline, unchanged.

### Chat Persistence
- Added `backend/app/models/chat_session.py` (`ChatSession`) and
  `backend/app/models/chat_message.py` (`ChatMessage`) — persisted,
  multi-turn conversations per document. `ChatSession` deliberately
  carries no `user_id`; ownership is derived via
  `session → document → document.user_id`, mirroring `DocumentChunk`'s
  own "no redundant FK" precedent. `ChatMessage.sequence_number` is
  application-assigned (0-based), not `created_at`-based, with a
  composite `UniqueConstraint(chat_session_id, sequence_number)` as
  the database-level guarantee.
- Added migration `f54da3d255ec_add_chat_sessions_and_messages` —
  both tables, `ON DELETE CASCADE` on both FKs, the composite unique
  constraint. Autogenerated, hand-reviewed (only the two intended
  tables detected, no drift), applied.
- Added `backend/app/services/chat_session_service.py` —
  `create_session`, `list_sessions_for_document`,
  `get_session_for_document` (the nested ownership check), `list_messages_for_session`,
  `append_message` (standalone, self-committing), and a private,
  non-committing `_stage_message()`.
- **A real atomicity bug was found and fixed before this milestone
  shipped.** The first implementation had `append_message()` commit
  internally inside the send-message flow — meaning a user's message
  would be durably persisted *before* the RAG call even ran. If RAG
  then failed, that message stayed committed from an
  already-finished transaction, with no way to undo it. Fixed by
  extracting `_stage_message()` (flush only), so the send-message
  route now stages the user message, calls RAG, stages the assistant
  reply only on success, and issues exactly one `db.commit()` —
  mirroring the exact "stage everything, one commit at the end"
  pattern `document_processing_service.process_document()` already
  established. Verified by a dedicated regression test at both the
  service level (`test_stage_message_does_not_commit_independently`)
  and the HTTP level
  (`test_send_message_llm_failure_persists_no_messages_atomicity`).
- Extended `backend/app/services/llm_service.py` —
  `generate_answer()`'s signature changed from `(system_prompt,
  user_prompt: str)` to `(system_prompt, messages: list[dict])`,
  using OpenAI's native multi-turn message format instead of folding
  history into one string. A real footgun was caught during
  implementation: a bare string is iterable, so an old-style caller
  would have silently corrupted data via `*messages` character-
  unpacking instead of raising an error — fixed with an explicit
  `TypeError` guard, plus a dedicated test.
- Extended `backend/app/services/rag_service.py` — the existing
  `answer_question()` is unchanged in behavior (all 10 pre-existing
  tests pass unmodified); a new `answer_question_with_history()`
  sits alongside it, accepting a `history: list[dict]` and
  truncating to the most recent `MAX_HISTORY_MESSAGES = 10` entries
  before sending to the LLM. Retrieval, grounding, and empty-context
  fallback behavior are identical between both functions.
- Added four new routes to `backend/app/api/documents.py`: `POST
  /{document_id}/chat/sessions` (create), `GET
  /{document_id}/chat/sessions` (list, paginated), `GET
  /{document_id}/chat/sessions/{session_id}/messages` (history), `POST
  /{document_id}/chat/sessions/{session_id}/messages` (send —
  atomic, per the fix above). Every session-level route nests two
  ownership checks — document ownership, then
  session-belongs-to-that-document — collapsing to an identical
  `404`, since a session ID alone is guessable/enumerable.
  `LLMProviderError → 502` mirrors the existing precedent exactly.
  The original stateless `POST /{document_id}/chat` is completely
  unchanged.
- Extended `backend/app/schemas/chat.py` with `ChatSessionResponse`,
  `ChatMessageResponse`, and `SendMessageRequest` — all narrow, never
  exposing embeddings, prompts, or provider metadata.
- Added `backend/tests/test_chat_session_model.py` (11 tests),
  `backend/tests/test_chat_session_service.py` (21 tests, including
  the atomicity regression tests), and
  `backend/tests/test_chat_persistence_api.py` (34 tests, including
  mandatory cross-user IDOR tests proving a session is unreachable
  both via another user's own document and via the session's actual
  owning document when requested by a different user, and that 404
  responses for nonexistent vs. unauthorized sessions are
  indistinguishable). Extended `backend/tests/test_llm_service.py`
  (+2) and `backend/tests/test_rag_service.py` (+8, covering history
  forwarding, role/order preservation, truncation, and the
  unchanged-`answer_question()` regression check).
- **287 backend tests passing overall, zero regressions.** No real
  OpenAI API call anywhere in the suite; `alembic current`/`heads`
  both `f54da3d255ec (head)`, `alembic check` → "No new upgrade
  operations detected."
- No frontend change, no session titles, no message
  editing/deletion, no conversation summarization, no streaming, no
  Redis/background workers, no multi-document sessions, no citations
  schema — all confirmed still out of scope and unimplemented.

### Frontend Chat UI
- Added `frontend/src/pages/ChatPage.tsx` — a protected chat
  workspace at `/documents/:documentId/chat`, consuming the four
  Chat Persistence endpoints. Single file, matching
  `DocumentsPage.tsx`'s existing one-file-per-feature precedent — no
  premature component extraction. Session sidebar (list, "+ New
  Chat", select-to-switch), a conversation view distinguishing user
  vs. assistant turns, and a composer (Enter to send, Shift+Enter for
  a newline, disabled while a request is in flight).
- Added `frontend/src/services/chat.ts`
  (`createChatSession`/`listChatSessions`/`getChatMessages`/`sendChatMessage`)
  and `frontend/src/types/chat.ts` (`ChatSessionResponse`,
  `ChatMessageResponse`) — mirroring `services/document.ts`'s and
  `types/document.ts`'s existing conventions exactly.
- Added `postAuthBody()` to `frontend/src/services/api.ts` — the one
  genuinely missing piece found by inspection: the existing
  `postAuth()` had no request-body parameter, but session creation
  needs an authenticated POST with no body while sending a message
  needs one with a JSON body. Mirrors `post()`'s existing JSON
  handling plus `postAuth()`'s bearer-token header; no new HTTP
  client was introduced.
- Added `getDocument()` to `frontend/src/services/document.ts` —
  reuses the existing `GET /documents/{document_id}` endpoint to show
  the document's filename in the chat page header; no new backend
  endpoint was created merely to display a title.
- Added an "Open Chat" button to `frontend/src/pages/DocumentsPage.tsx`
  and a new protected route (`ROUTES.chat` / `chatPath()`) in
  `frontend/src/App.tsx` and `frontend/src/constants/routes.ts` — the
  only way to reach the chat workspace from the existing document
  list, using the existing `ProtectedRoute` mechanism unchanged.
- **Server is the sole source of truth — no client-side conversation
  store.** Sending a message optimistically shows the user's turn as
  a pending UI placeholder, then re-fetches the real, persisted
  transcript from the API once the request settles (or removes the
  placeholder on failure) — the placeholder is never trusted as the
  final record. A refresh, logout/login, or switching sessions always
  reloads from the server.
- `401` reuses the exact `ApiError`/logout-and-redirect pattern
  already established in `DocumentsPage.tsx`. `404`, `422`, and `502`
  are shown as plain, non-technical messages; no stack traces, API
  keys, or raw provider/database errors are ever rendered.
- **No backend files were modified.** No new dependency was added —
  no Markdown renderer, no state-management library, no HTTP client,
  no animation library; `package.json` confirmed unchanged before
  starting (no React Query, no CSS framework already present to
  justify adding one).
- **A real, pre-existing backend gap was found during manual E2E
  verification, and deliberately not fixed here.** The persisted
  send-message route
  (`POST /documents/{document_id}/chat/sessions/{session_id}/messages`)
  catches `LLMProviderError` but not `EmbeddingProviderError` — a
  query-embedding failure (e.g. no `OPENAI_API_KEY` configured)
  surfaces as a raw `500` instead of the documented `502`. Confirmed
  via a real request against a running local server; traceback shows
  `embedding_service._get_client()` raising `openai.OpenAIError`,
  uncaught by the router's `except llm_service.LLMProviderError`
  clause. Not fixed in this milestone, per its explicit
  frontend-only scope boundary — the frontend still degrades
  gracefully (a generic "Request failed: 500" message, no crash, no
  leaked internals) via the existing `ApiError`/`extractErrorMessage`
  path regardless.
- **Verification:** `npm run build` (`tsc -b && vite build`) passes
  cleanly. `npm run lint` (oxlint) — 0 warnings, 0 errors across all
  21 frontend source files. Full backend suite re-run: **288
  passed**, zero regressions, confirming no backend behavior was
  affected. Manual end-to-end verification performed via `curl`
  against a real running FastAPI server (not simulated): signup →
  login → document upload → `GET /documents/{id}` → session creation
  → session listing → message-history retrieval → `422` (blank
  question) → `401` (no token) all confirmed matching the frontend's
  exact expectations. A full real-provider round trip (send a
  question, receive an LLM-backed answer) could not be executed in
  this sandbox — no `OPENAI_API_KEY` is configured here, and
  `/process` itself requires the embedding provider — so document
  processing, and therefore a populated, chat-ready document, could
  not be produced. This is an environment limitation, not a
  frontend defect; no successful provider call was fabricated or
  claimed.
- No streaming, no WebSockets/SSE, no message editing/deletion, no
  session titles/renaming, no conversation summarization, no
  citations/source cards, no PDF viewer, no multi-document chat, no
  new authentication mechanism, no deployment — all confirmed still
  out of scope and unimplemented.

### Persistent Cloud Document Storage (Cloudflare R2)
- Rewrote `backend/app/services/storage_service.py` from synchronous
  local-disk I/O to async Cloudflare R2 (S3-compatible object
  storage) access via `aioboto3` — local disk on a Render/Railway-style
  container is not durable across redeploys/restarts, so an uploaded
  PDF (and the ability to reprocess it) would silently vanish on
  every deploy. `Document.storage_path` is unchanged in column
  type/name — it now holds an R2 object key instead of a filesystem
  path, so no migration was needed. Two distinct read functions
  remain (`get_file_bytes()` for the download endpoint,
  `get_file_path()` for `parse_service`'s Path-based contract,
  deliberately not touched by this milestone).
- **Fixed a real bug found during inspection, not introduced by this
  milestone's own work:** `document_service.py`'s three calls to
  `storage_service.save_file()`/`get_file_path()`/`delete_file()`
  were missing `await` — since those functions had already been made
  async in an earlier pass, uploads/downloads/deletes were silently
  broken. Also switched `get_document_file_for_user()` from
  `get_file_path()` to `get_file_bytes()`, matching
  `storage_service.py`'s own documented intent.
- **Fixed a second real bug found during inspection:** the download
  route in `documents.py` still used FastAPI's `FileResponse(path=...)`,
  which requires a real local filesystem path — incompatible with R2.
  Rewritten to build a plain `Response` from raw bytes, with an
  explicit `Content-Disposition` header replicating exactly what
  `FileResponse`'s own `filename=` parameter used to generate
  automatically.
- Added `aioboto3` to `backend/pyproject.toml` — the only new
  dependency, used exclusively inside `storage_service.py`. Async,
  not synchronous `boto3`, since every other I/O path in this
  codebase (SQLAlchemy, the OpenAI SDK) is genuinely async.
- Updated `backend/.env.example`: removed `UPLOAD_DIR`, added
  `R2_ENDPOINT_URL`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/`R2_BUCKET_NAME`
  placeholders (no real values). `app/core/config.py` already had
  these four settings from earlier work — confirmed, not re-added.
- **Testing required migrating off local-disk assumptions across the
  whole suite, not just one file.** Added a shared, autouse
  `_mock_r2_storage` fixture to `tests/conftest.py` — an in-memory
  fake R2 client (mirroring this project's existing
  `embedding_service`/`llm_service` fake-client pattern) so every
  test that uploads/downloads/processes/deletes a document
  transparently works without a real network call. Removed a now-broken
  `_isolated_upload_dir` fixture (referencing the removed
  `settings.upload_dir`) from 9 test files. Rewrote local-disk
  manipulation in `test_documents_api.py` (7 locations),
  `test_document_process_api.py`, `test_document_text_service.py` (2
  tests), and `test_document_processing_service.py` (4 tests) to use
  the fake R2 store instead of `Path`/`unlink`/`write_bytes`.
  Completely rewrote `test_storage_service.py` (23 tests) for the
  async R2 API.
- Fixed stale "disk"/"filesystem" wording in `docs/API.md` (6
  locations) and a stale "Railway for backend/worker/Postgres/Redis"
  line in `docs/PROJECT_CONTEXT.md` left over from an early planning
  snapshot, contradicting this project's actual, current
  no-Redis/no-worker architecture.
- **293 backend tests passing overall (288 pre-existing/pre-fix +
  net new from the storage_service rewrite), zero regressions.**
  `alembic current`/`heads` both `f54da3d255ec (head)`, `alembic
  check` — "No new upgrade operations detected" (no schema change —
  `storage_path` was always a plain string column). No real R2 or
  OpenAI API call anywhere in the automated suite.
- **No Render/Vercel/Neon deployment performed.** This milestone is
  application-code preparation only. One known, small deployment
  blocker deliberately left unfixed here (out of this milestone's
  scope): `backend/Dockerfile`'s `CMD` hard-codes `--port 8000`
  instead of reading `$PORT`, which Render (and Railway) require at
  runtime.
- R2 bucket remains private throughout — no public URLs, no
  presigned-URL redirects; the backend proxies all file bytes through
  its own authenticated endpoints, preserving the exact
  ownership-isolation guarantees already established for local disk.
  R2 credentials are backend-only environment variables — never
  exposed to the frontend, Vercel, or committed to Git.
