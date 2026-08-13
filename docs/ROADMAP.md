# Roadmap

Full phase breakdown lives in the Phase 0 planning document. Summary:

- **Phase 1** (current, in progress — target end state): single-document upload + chat, deployed.
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

NEXT (not yet approved/designed):
- RAG (retrieval + prompt assembly + LLM call)
- Single-document chat
- Vector index (HNSW/IVFFlat) — once multi-document/large-scale retrieval genuinely needs one
- Multi-document search (Phase 2)
