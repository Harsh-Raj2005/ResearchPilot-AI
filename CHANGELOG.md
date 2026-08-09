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
  `git diff --stat`/`--name-only`/`--check` confirmed the implementation change surface is exactly `app/api/documents.py` (modified) plus `tests/test_document_process_api.py` (new); the checkpoint also updates the five project documentation files listed above, with no whitespace
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
