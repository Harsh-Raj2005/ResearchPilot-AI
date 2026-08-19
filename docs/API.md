# API Documentation

Base URL: `/api/v1`

- **Production:** `https://researchpilot-ai-jccb.onrender.com/api/v1/...`
- **Local dev:** `http://localhost:8000/api/v1/...`

**There is no logout endpoint.** Logging out is entirely client-side —
the frontend's `AuthContext.logout()` clears the stored access token.
The backend issues stateless JWTs and holds no server-side session to
invalidate.

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
**Response `413`** — file exceeds `MAX_UPLOAD_SIZE_MB` (default 20MB). Nothing is uploaded to storage when this happens.
**Response `422`** — file extension not in the allowed list (`.pdf`, `.docx`, `.txt`). Nothing is uploaded to storage when this happens.
**Response `500`** — a storage-provider error occurred while saving the file (network failure, credential/permission error, provider outage).

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
- The body is built from raw bytes fetched from Cloudflare R2 and returned via a plain Starlette `Response` — not `FileResponse`, which requires a real local filesystem path. Range requests are therefore not supported; the whole file is returned in one response.

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user. Identical to the detail endpoint's 404 behavior — indistinguishable from a nonexistent ID.
**Response `422`** — `document_id` is not a valid UUID
**Response `500`** — the document row exists and is owned by the caller, but its object is missing from storage (e.g. manually deleted, bucket reset). The response never includes the storage object key or bucket name.

### `DELETE /documents/{document_id}`
Delete a document owned by the current user — removes both the stored file and the database record.

**Path parameter:** `document_id` (UUID)

**Response `204`** — no body. The file is deleted first, then the database record; see `docs/PROJECT_CONTEXT.md` for the full data-integrity reasoning behind this ordering. Deleting a document whose underlying file was already missing (e.g. manually removed) still succeeds and removes the stale database record — `storage_service.delete_file()` treats an already-missing file as success, not an error.

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user. Identical behavior to detail/download. **A second `DELETE` of an already-deleted document also returns `404`** (not another `204`) — once deleted, the document genuinely no longer exists for this user.
**Response `422`** — `document_id` is not a valid UUID
**Response `500`** — the file exists but could not be deleted for a genuine storage-provider reason (not "already missing" — see above). The database record is deliberately left untouched in this case.

_This completes the full Document Management CRUD surface (upload, list, detail, download, delete)._

### `POST /documents/{document_id}/process`
Parse a document owned by the current user, persist the extracted text, split it into ordered chunks, and generate an embedding for each chunk (Document Text Extraction Checkpoint 5; chunk persistence added by the Document Chunking milestone; embeddings added by the Document Chunks → Embeddings milestone). This is an **explicit, on-demand operation** — upload does **not** automatically trigger it.

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
The response is the same `DocumentResponse` shape used by upload/list/detail — it **never** includes the extracted text (`DocumentText.content`), the resulting chunks, or their embeddings. Extracted text, chunks, and embeddings all remain internal processing state, not something any current endpoint serves back to the client.

Text extraction, chunking, and embedding generation all happen together, atomically: the extracted-text row, the chunk rows, and their embeddings are persisted in a single transaction. Embeddings are generated via OpenAI `text-embedding-3-small` (1536 dimensions), synchronously, before that transaction commits. If the embedding provider fails (or any earlier step fails), nothing durably changes — the previously committed extracted text, chunks, and embeddings (if any) are left exactly as they were. A document with empty extracted text produces zero chunks and makes no embedding request at all.

Calling this endpoint again for an already-processed document **reprocesses** it — the extracted-text row is updated in place, and all of its chunks (with fresh embeddings) are deleted and recreated from the new text (not updated in place, since chunk count can change between reprocessings). The previous chunks/embeddings are only deleted once the new embeddings have already been generated successfully, so a reprocessing attempt whose embedding call fails never destroys the prior, still-valid state. There is no separate reprocess endpoint; this one endpoint serves both purposes.

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user. Identical behavior to detail/download/delete — indistinguishable from a nonexistent ID.
**Response `422`** — `document_id` is not a valid UUID, *or* the document's stored file has an unsupported format for parsing (currently PDF-only), *or* the file exists but is corrupted/invalid and cannot be parsed.
**Response `500`** — the document row exists and is owned by the caller, but its stored object is missing from storage (a server-side data-integrity problem, not a client error — identical in spirit to download's `500` for the same condition).
**Response `502`** — the embedding provider failed (network error, timeout, API error, or an unexpected/malformed response). The response body is a generic message only — no raw provider error text, response body, or API key ever reaches the client.

_Chunking, embeddings, retrieval, RAG, and chat are all implemented on top of this endpoint — see the Chat sections below. A document must be processed through this endpoint before chat can find any relevant content in it; an unprocessed document has no chunks, so chat returns the deterministic "no relevant content" fallback._

---

## Chat (RAG)

### `POST /documents/{document_id}/chat`
Ask one question about one document owned by the current user. Stateless — a single question/answer exchange, not a conversation. Exposes the existing internal RAG pipeline (`rag_service.answer_question()`) through a thin authenticated endpoint.

All routes below require `Authorization: Bearer <access_token>` (via `get_current_user`). A missing or invalid token returns `401`.

**Path parameter:** `document_id` (UUID)

**Request**
```json
{ "question": "What is the main contribution of this paper?" }
```
`question` must not be missing, empty, or whitespace-only.

**Response `200`**
```json
{ "answer": "..." }
```
The response contains only the answer text — never retrieved chunks, chunk IDs, cosine distances, embeddings, prompts, or any OpenAI provider metadata. Citations/sources are not part of this response; that is an explicitly deferred, separate future milestone.

If the document has no relevant chunks (e.g. it has not been processed yet, or its extracted text was empty), the response is still `200` with a deterministic fallback answer explaining that no relevant document context was found — the RAG service never calls the LLM in that case.

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user. Identical behavior to every other `{document_id}` route — indistinguishable from a nonexistent ID.
**Response `422`** — `document_id` is not a valid UUID, *or* the request body is missing `question`, *or* `question` is empty or whitespace-only.
**Response `502`** — the LLM provider failed, *or* the query-embedding step failed (network error, timeout, API error, or an unexpected/malformed response). Both `LLMProviderError` and `EmbeddingProviderError` map to `502` here. The response body is a generic message only — no raw provider error text, response body, or API key ever reaches the client.

_This is a stateless single-question endpoint — there is no conversation history, session, or memory. Persistent, multi-turn conversations are a separate surface, documented under "Chat Persistence" below; both exist side by side, and the frontend chat UI uses the persistent one._

---

## Chat Persistence (sessions and messages)

A persisted, multi-turn conversation about one document. Unlike `POST /documents/{document_id}/chat` above (a single stateless question), these four endpoints let a client create a named conversation, send a sequence of messages to it, and retrieve the full transcript. All require `Authorization: Bearer <access_token>`.

Every route below performs two nested ownership checks: first that `document_id` exists and is owned by the caller (identical to every other `{document_id}` route), then — for the two `{session_id}` routes — that the session actually belongs to that document. A session that exists but belongs to a different document (even one owned by the same user) returns the same `404` as a session that does not exist at all; a session ID alone is never sufficient to reach a session's data.

### `POST /documents/{document_id}/chat/sessions`
Create a new, empty chat session for a document.

**Response `201`**
```json
{ "id": "uuid", "created_at": "2026-01-01T00:00:00Z" }
```

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user
**Response `422`** — `document_id` is not a valid UUID

### `GET /documents/{document_id}/chat/sessions`
List a document's chat sessions, newest first. Plain `skip`/`limit` pagination, same convention as `GET /documents`.

**Response `200`**
```json
[ { "id": "uuid", "created_at": "2026-01-01T00:00:00Z" } ]
```

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user

### `GET /documents/{document_id}/chat/sessions/{session_id}/messages`
Return a chat session's full transcript, in conversation order.

**Response `200`**
```json
[
  { "id": "uuid", "role": "user", "content": "What is the main contribution?", "sequence_number": 0, "created_at": "..." },
  { "id": "uuid", "role": "assistant", "content": "...", "sequence_number": 1, "created_at": "..." }
]
```

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user, *or* no session with this ID exists under this document (including a session that genuinely exists but belongs to a different document)
**Response `422`** — `document_id` or `session_id` is not a valid UUID

### `POST /documents/{document_id}/chat/sessions/{session_id}/messages`
Send a message within a persisted chat session. Persists the user's message, calls the RAG pipeline with the session's prior transcript (bounded to the most recent messages — older turns are truncated, not summarized), persists the assistant's reply, and returns the assistant's message. The user's own message is not echoed back in the response — the client already has its content from the request it just sent; it is visible on the next `GET .../messages` call.

The user's message and the assistant's reply are persisted atomically: if the LLM call fails, neither message is durably saved — there is never a committed state with a user question but no assistant reply, and never a fabricated assistant reply on its own.

**Request**
```json
{ "question": "And what about the methodology?" }
```
`question` must not be missing, empty, or whitespace-only.

**Response `201`**
```json
{ "id": "uuid", "role": "assistant", "content": "...", "sequence_number": 3, "created_at": "..." }
```

**Response `401`** — missing or invalid token
**Response `404`** — no document with this ID exists, *or* it exists but belongs to a different user, *or* no session with this ID exists under this document
**Response `422`** — `document_id`/`session_id` is not a valid UUID, *or* `question` is missing, empty, or whitespace-only
**Response `502`** — the LLM provider failed, *or* the query-embedding step failed. Both `LLMProviderError` and `EmbeddingProviderError` map to `502` on this route. Generic message only — no raw provider error text, response body, or API key ever reaches the client. Atomicity is unaffected either way: the staged user message is rolled back, so no partial conversation is committed.

_These endpoints are the persistence layer on top of the same RAG pipeline `POST /documents/{document_id}/chat` already uses — that stateless endpoint is unchanged and remains available alongside sessions. The frontend chat UI (`/documents/:documentId/chat`) consumes these four endpoints._
