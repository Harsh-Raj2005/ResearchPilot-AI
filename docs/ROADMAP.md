# Roadmap

Full phase breakdown lives in the Phase 0 planning document. Summary:

- **Phase 1** (current): single-document upload + chat, **deployed — target end state reached**. Live at https://research-pilot-ai-ashy.vercel.app/ (frontend, Vercel) and https://researchpilot-ai-jccb.onrender.com/ (backend, Render).
- **Phase 2**: multiple documents, semantic search, collections.
- **Phase 3**: notes, highlights, tags, GROBID metadata extraction.
- **Phase 4**: multi-paper comparison (candidate point to introduce Qdrant).
- **Phase 5**: literature review generation.
- **Phase 6+**: research gap detection, knowledge graph, recommendations,
  dashboard, collaboration, production hardening.

## Phase 1 task log

- [x] Task 1 — Project skeleton
- [x] Task 2.1 — Database foundation
- [x] Task 2.2 — Authentication foundation (backend)
- [x] Task 2.3 — Frontend authentication
- [x] Task 3A — `get_current_user` protected-route dependency
- [x] Task 3B — Document management (backend, upload-only scope)
  - [x] Checkpoint 1 — Document data layer (model + migration)
  - [x] Checkpoint 2 — Storage service (local disk, UUID filenames, extension validation)
  - [x] Checkpoint 3 — Document upload API (`get_current_user`'s first real consumer)
  - [x] Checkpoint 4 — Housekeeping (env, .gitignore, docker-compose volume, docs)
- [ ] Document list/detail/download/delete — not scheduled to a task yet
  - [x] List — `GET /api/v1/documents`, paginated, own documents only
  - [x] Detail — `GET /api/v1/documents/{document_id}`, 404 for not-owned/nonexistent (indistinguishable)
  - [x] Download — `GET /api/v1/documents/{document_id}/file`, same ownership isolation, streamed via FastAPI FileResponse
  - [x] Delete — `DELETE /api/v1/documents/{document_id}`, file deleted before DB row (see PROJECT_CONTEXT.md for ordering rationale)
- [x] Document Management CRUD — **complete** (upload, list, detail, download, delete)
- [x] Document Text Extraction
  - [x] Checkpoint 1 — Standalone PDF parse service (`app/services/parse_service.py`), not wired into upload
  - [x] Checkpoint 2 — Schema/storage design reviewed and approved (separate `document_texts` table, 1:0..1)
  - [x] Checkpoint 3 — `document_texts` table implemented (schema only; nothing writes to it yet)
  - [x] Checkpoint 4 — Parse -> persist integration (`document_text_service.py`, not wired into upload)
  - [x] Checkpoint 5 — Explicit processing endpoint (`POST /documents/{document_id}/process`), decided over automatic upload wiring; upload remains unchanged; reprocessing supported via the existing upsert behavior
- [x] Document Text Extraction — **complete** (parse service, `document_texts` schema, parse-to-persist integration, explicit processing endpoint — upload itself still does not auto-parse, by design)
- [x] Task 3C — Document management (frontend)
  - [x] Protected `/documents` route (unauthenticated visitors redirected to `/login`)
  - [x] Document list, paginated (`skip`/`limit`, "Load more")
  - [x] Upload
  - [x] Download
  - [x] Delete
  - [x] Explicit "Process" action, wired to `POST /documents/{document_id}/process`
  - No PDF viewer, chat, or research-workspace UI — those remain out of scope
- [x] Document Chunking
  - [x] `document_chunks` table (FK'd to `document_texts`, composite `UNIQUE(document_text_id, chunk_index)`)
  - [x] `chunk_service.py` — deterministic, character-based chunking algorithm (paragraph-aware, target 1000 / max 1200 / overlap 150 chars), no tokenizer/NLP dependency
  - [x] `document_processing_service.py` — new orchestration boundary; `/process` now persists `DocumentText` and `DocumentChunk` atomically in one transaction
  - [x] Chunks remain fully internal — no new endpoint, no frontend change, never exposed by any response
  - No embeddings, pgvector, vector search, RAG, or chat — those remain later, separate milestones
- [x] Document Chunks -> Embeddings
  - [x] `document_chunks.embedding` column (`Vector(1536)`, `NOT NULL`, OpenAI `text-embedding-3-small`)
  - [x] `embedding_service.py` — encapsulates the OpenAI async SDK, single `embed_texts()` function, no provider abstraction
  - [x] `document_processing_service.py` extended: embeddings generated synchronously inside `/process`, before the single commit, so text/chunks/embeddings stay atomic together
  - [x] No vector index yet, no Redis/background worker, no automatic retries, no embedding metadata columns
  - [x] All provider calls mocked in tests — no real OpenAI API call in the test suite
  - No semantic search, retrieval, RAG, or chat — those remain later, separate milestones
- [x] Vector Retrieval
  - [x] `retrieval_service.py` — internal-only `retrieve_similar_chunks()` primitive, pgvector cosine-distance search scoped to an already-authorized `Document`
  - [x] `embedding_service.py` extended with `embed_query()`, a thin single-text wrapper around the existing `embed_texts()`
  - [x] No new endpoint, no new migration, no vector index — deliberately deferred
  - [x] Document isolation enforced by construction (join through `DocumentText.document_id`, no cross-document leakage possible)
  - [x] All provider calls mocked in tests — deterministic unit-vector fixtures, no real OpenAI API call
- [x] RAG Foundation
  - [x] `rag_service.py` — internal-only `answer_question()` primitive; composes `embed_query()` + `retrieve_similar_chunks()` + `llm_service.generate_answer()`
  - [x] `llm_service.py` — encapsulates the OpenAI Chat Completions call, single `generate_answer()` function, no provider abstraction
  - [x] Grounded system prompt: answers only from retrieved context, explicit "insufficient context" fallback, retrieved text treated as untrusted reference material
  - [x] Empty retrieval returns a deterministic fallback answer without calling the LLM
  - [x] No new endpoint (Stage A only — a public endpoint is a separate future decision), no new migration, no chat persistence, no frontend change
  - [x] All provider calls mocked in tests — no real OpenAI API call, no OPENAI_API_KEY required
- [x] Single-Document Chat API (Stage B)
  - [x] `POST /api/v1/documents/{document_id}/chat` — thin authenticated endpoint exposing the existing internal `rag_service.answer_question()` pipeline
  - [x] `backend/app/schemas/chat.py` — `ChatRequest`/`ChatResponse`; empty/whitespace-only question rejected at the schema boundary (`422`)
  - [x] Ownership via the existing `document_service.get_document_for_user()` — nonexistent and unauthorized documents both `404`, indistinguishable
  - [x] `rag_service.answer_question()` treated as a black box — no RAG logic, retrieval, or LLM calls in the router
  - [x] `LLMProviderError` → `502`, mirroring the existing `EmbeddingProviderError` → `502` precedent
  - [x] No database migration, no chat persistence, no frontend change
  - [x] All RAG/LLM calls mocked in tests — no real OpenAI API call, no OPENAI_API_KEY required
- [x] Chat Persistence
  - [x] `ChatSession`/`ChatMessage` models — sessions belong to a `Document` (no redundant `user_id`), messages belong to a `ChatSession`, both `ON DELETE CASCADE`
  - [x] `ChatMessage.sequence_number` — application-assigned ordering, `UniqueConstraint(chat_session_id, sequence_number)` enforced at the DB level
  - [x] `chat_session_service.py` — session/message CRUD; nested ownership check (`get_session_for_document`) so a session ID alone can never be reached without also owning its parent document
  - [x] `llm_service.generate_answer()` extended to accept a full message list (native multi-turn), not just one prompt string — `rag_service.answer_question()`'s existing stateless behavior is unchanged
  - [x] `rag_service.answer_question_with_history()` — new, alongside the unchanged `answer_question()`; truncates to the most recent `MAX_HISTORY_MESSAGES` (10) prior turns
  - [x] Four new endpoints: create session, list sessions, list messages, send message — all under `/documents/{document_id}/chat/sessions...`
  - [x] Atomic send-message persistence: user message and assistant reply staged together, one commit only after the LLM call succeeds — no partial state on failure
  - [x] No new frontend, no vector index change, no summarization
  - [x] All RAG/LLM calls mocked in tests — no real OpenAI API call, no OPENAI_API_KEY required
- [x] Frontend Chat UI
  - [x] Protected route `/documents/:documentId/chat`, "Open Chat" link from the documents list
  - [x] `services/chat.ts`, `types/chat.ts` — createChatSession, listChatSessions, getChatMessages, sendChatMessage
  - [x] Session sidebar (list, new-chat, select), conversation view (user/assistant distinction, empty states), composer (Enter-to-send, Shift+Enter newline, disabled while sending)
  - [x] Server is the source of truth — no client-side conversation store; refresh/logout/login all reload from the API
  - [x] 401/404/422/502 handled via the existing ApiError mechanism; no backend changes
  - [x] Frontend build + lint pass; full backend suite re-run, zero regressions
- [x] Persistent Cloud Document Storage (Cloudflare R2)
  - [x] `storage_service.py` rewritten for async R2 access via `aioboto3`; `Document.storage_path` now holds an R2 object key (no schema change — it was always a plain string)
  - [x] `document_service.py` fixed to `await` the now-async storage calls; download path switched to `get_file_bytes()`
  - [x] Download route rewritten to build a `Response` from bytes instead of `FileResponse` (no more local filesystem streaming)
  - [x] `aioboto3` added to `pyproject.toml`; `.env.example` updated with `R2_ENDPOINT_URL`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/`R2_BUCKET_NAME` placeholders, `UPLOAD_DIR` removed
  - [x] Shared, autouse fake-R2 fixture added to `conftest.py`; full test suite migrated off local-disk assumptions (`test_storage_service.py` rewritten; 4 other test files fixed)
  - [x] Application code only — no actual Render/Vercel/Neon deployment performed *(true at the time of that milestone; the deployment has since happened — see below)*
- [x] Pre-deployment backend fixes — **verified present in the current source**
  - [x] `EmbeddingProviderError` → `502` on the persisted send-message route (commit `8c7a542`, "fix: map embedding failures to 502") — closes the gap previously listed under NEXT
  - [x] `backend/Dockerfile`'s `CMD` binds `${PORT:-8000}` in shell form instead of a hard-coded `--port 8000`, so Render's injected `$PORT` is honored while local `docker run`/Compose still gets 8000
- [x] Production deployment — **live**
  - [x] Frontend on Vercel — https://research-pilot-ai-ashy.vercel.app/
  - [x] FastAPI backend on Render — https://researchpilot-ai-jccb.onrender.com/
  - [x] Database on Neon (PostgreSQL + pgvector)
  - [x] Document storage on Cloudflare R2 (private bucket, backend-proxied)
  - [x] OpenAI for embeddings and LLM generation
- [x] Production SPA Routing + Logout UI (branch `fix/vercel-routing-and-logout`, commit `511ae84`, merged into `main`)
  - [x] `frontend/vercel.json` — single SPA rewrite so `/login`, `/signup`, `/documents`, and `/documents/:documentId/chat` load on direct navigation or refresh instead of a Vercel 404
  - [x] Visible "Log out" action in the Documents workspace header
  - [x] Visible "Log out" action in the Chat workspace header, preserving the existing "← Documents" navigation and document title
  - [x] Reuses the existing `AuthContext` logout and `ROUTES.login` navigation — no new authentication mechanism, no backend/API/database change
  - [x] Frontend build and lint pass; production routes and document-grounded chat manually verified after deployment

NEXT (not yet approved/designed):
- Conversation summarization for long sessions
- Vector index (HNSW/IVFFlat) — once multi-document/large-scale retrieval genuinely needs one
- Citations/sources in chat answers
- Phase 2 — multi-document/global semantic search, collections. **Not started.**

*(Two items previously listed here have moved into the completed log
above: the actual Render + Vercel + Neon deployment, and the
`EmbeddingProviderError` → `502` mapping on the persisted send-message
route.)*

Still deferred, unchanged — these are **not** complete and must not be
marked so merely because they were planned: multi-document/global
semantic search and collections (Phase 2), multi-paper comparison
(Phase 4), literature review generation (Phase 5), research gap
detection (Phase 6), knowledge graph (Phase 7), and everything else in
the phase list at the top of this document.
