# API Documentation

Base URL: `/api/v1`

## Health

### `GET /health`
Returns application and database status.

**Response `200`**
```json
{ "status": "ok", "app": "ResearchPilot AI", "environment": "development", "database": "ok" }
```

**Response `503`** (database unreachable)
```json
{ "detail": { "status": "error", "database": "unreachable", "error": "..." } }
```

---

## Auth

### `POST /auth/signup`
Create a new user account.

**Request**
```json
{ "email": "user@example.com", "username": "someuser", "password": "at-least-8-chars" }
```

**Response `201`**
```json
{ "id": "uuid", "email": "user@example.com", "username": "someuser", "is_active": true, "created_at": "2026-01-01T00:00:00Z" }
```

**Response `409`** — email or username already registered
**Response `422`** — validation failure (password too short, invalid email, password exceeds 72 bytes when UTF-8 encoded)

### `POST /auth/login`
Exchange credentials for a JWT access token.

**Request**
```json
{ "email": "user@example.com", "password": "at-least-8-chars" }
```

**Response `200`**
```json
{ "access_token": "eyJ...", "token_type": "bearer" }
```

**Response `401`** — invalid email or password (deliberately the same error for both cases, and for an inactive account, so a failed login never reveals which part was wrong)

---

## Documents

All routes below require `Authorization: Bearer <access_token>` (via `get_current_user`). A missing or invalid token returns `401`.

### `POST /documents/upload`
Upload a PDF, DOCX, or TXT file (multipart/form-data).

**Request** — `multipart/form-data` with a `file` field.

**Response `201`**
```json
{
  "id": "uuid",
  "original_filename": "thesis.pdf",
  "content_type": "application/pdf",
  "file_size_bytes": 123456,
  "created_at": "2026-01-01T00:00:00Z"
}
```
Note: the response never includes `stored_filename` or `storage_path` — those are internal storage details, not exposed to clients.

**Response `401`** — missing or invalid token
**Response `413`** — file exceeds `MAX_UPLOAD_SIZE_MB` (default 20MB). Nothing is written to disk when this happens.
**Response `422`** — file extension not in the allowed list (`.pdf`, `.docx`, `.txt`). Nothing is written to disk when this happens.
**Response `500`** — a filesystem error occurred while saving the file (disk full, permission denied, etc.)

_Validation is extension-based only for Phase 1 — the declared `Content-Type` is trusted as-is and stored for display, not independently verified against file content (`python-magic` content-sniffing is deferred to Phase 12)._

### `GET /documents`
List the current user's documents, newest first.

**Query parameters**
| Param | Default | Bounds |
|---|---|---|
| `skip` | `0` | `>= 0` |
| `limit` | `20` | `1`–`100` |

**Response `200`**
```json
[
  {
    "id": "uuid",
    "original_filename": "thesis.pdf",
    "content_type": "application/pdf",
    "file_size_bytes": 123456,
    "created_at": "2026-01-01T00:00:00Z"
  }
]
```
Returns `[]` (not an error) for a user with no documents. Results are always scoped to the authenticated user — there is no way to list another user's documents through this endpoint.

**Response `401`** — missing or invalid token
**Response `422`** — `skip`/`limit` outside their allowed bounds

### `GET /documents/{document_id}`
Return metadata for a single document owned by the current user.

**Path parameter:** `document_id` (UUID)

**Response `200`**
```json
{
  "id": "uuid",
  "original_filename": "thesis.pdf",
  "content_type": "application/pdf",
  "file_size_bytes": 123456,
  "created_at": "2026-01-01T00:00:00Z"
}
```

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user. Both cases return the identical response — the endpoint never reveals whether another user's document exists.
**Response `422`** — `document_id` is not a valid UUID

### `GET /documents/{document_id}/file`
Return the actual stored file for a document owned by the current user.

**Path parameter:** `document_id` (UUID)

**Response `200`** — the raw file bytes.
- `Content-Type` — the document's stored `content_type`
- `Content-Disposition: attachment; filename="<original_filename>"` — always the user's original filename, never the internal UUID-based stored filename
- `Accept-Ranges: bytes` — range requests work automatically (Starlette's built-in `FileResponse` behavior, not custom code)

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user. Identical to the detail endpoint's 404 behavior — indistinguishable from a nonexistent ID.
**Response `422`** — `document_id` is not a valid UUID
**Response `500`** — the document row exists and is owned by the caller, but its file is missing from disk (e.g. manually deleted, volume reset). The response never includes the server-side filesystem path.

### `DELETE /documents/{document_id}`
Delete a document owned by the current user — removes both the stored file and the database record.

**Path parameter:** `document_id` (UUID)

**Response `204`** — no body. The file is deleted first, then the database record; see `docs/PROJECT_CONTEXT.md` for the full data-integrity reasoning behind this ordering. Deleting a document whose underlying file was already missing (e.g. manually removed) still succeeds and removes the stale database record — `storage_service.delete_file()` treats an already-missing file as success, not an error.

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user. Identical behavior to detail/download. **A second `DELETE` of an already-deleted document also returns `404`** (not another `204`) — once deleted, the document genuinely no longer exists for this user.
**Response `422`** — `document_id` is not a valid UUID
**Response `500`** — the file exists but could not be deleted for a genuine filesystem reason (not "already missing" — see above). The database record is deliberately left untouched in this case.

_This completes the full Document Management CRUD surface (upload, list, detail, download, delete)._

### `POST /documents/{document_id}/process`
Parse a document owned by the current user and persist the extracted text (Document Text Extraction Checkpoint 5). This is an **explicit, on-demand operation** — upload does **not** automatically trigger it.

**Path parameter:** `document_id` (UUID)

**Response `200`**
```json
{
  "id": "uuid",
  "original_filename": "thesis.pdf",
  "content_type": "application/pdf",
  "file_size_bytes": 123456,
  "created_at": "2026-01-01T00:00:00Z"
}
```
The response is the same `DocumentResponse` shape used by upload/list/detail — it **never** includes the extracted text (`DocumentText.content`) or any internal storage field. Extracted text remains internal processing state, not something any current endpoint serves back to the client.

Calling this endpoint again for an already-processed document **reprocesses** it — the underlying service upserts (updates the existing extracted-text row in place) rather than creating a duplicate. There is no separate reprocess endpoint; this one endpoint serves both purposes.

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user. Identical behavior to detail/download/delete — indistinguishable from a nonexistent ID.
**Response `422`** — `document_id` is not a valid UUID, *or* the document's stored file has an unsupported format for parsing (currently PDF-only), *or* the file exists but is corrupted/invalid and cannot be parsed.
**Response `500`** — the document row exists and is owned by the caller, but its stored file is missing from disk (a server-side data-integrity problem, not a client error — identical in spirit to download's `500` for the same condition).

_Document parsing is now available on demand via this endpoint. Chunking, embeddings, RAG, and chat are not implemented — see the Phase 1 Technical Design Document and `docs/PROJECT_CONTEXT.md` for the full planned surface._
