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
