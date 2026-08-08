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

_Detail, download, and delete endpoints are not implemented yet — see the Phase 1 Technical Design Document and `docs/PROJECT_CONTEXT.md` for the full planned surface._
