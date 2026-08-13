# ResearchPilot AI

An AI-powered research assistant — upload papers, chat with them, and
(in later phases) compare, summarize, and reason across a whole
literature set.

Built incrementally, one phase and one task at a time. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the phase plan and
[`docs/API.md`](docs/API.md) for API documentation.

## Current status

**Phase 1: Document Management CRUD is complete.** Authenticated
document upload, listing, detail, download, and delete all work
end-to-end — `POST /api/v1/documents/upload`, `GET
/api/v1/documents`, `GET /api/v1/documents/{document_id}`, `GET
/api/v1/documents/{document_id}/file`, and `DELETE
/api/v1/documents/{document_id}` are all live, with consistent
ownership isolation and indistinguishable 404s throughout. A
standalone PDF text-extraction service (`app/services/parse_service.py`)
exists and is fully tested. Extracted text is persisted via a
`document_texts` table and `document_text_service.parse_and_store_document_text()`.
**Document Text Extraction Checkpoint 5 adds an explicit processing
endpoint, `POST /api/v1/documents/{document_id}/process`** — parsing
is triggered on demand by this endpoint, not automatically on upload.
Calling it again for an already-processed document reprocesses it
in place. Extracted text itself is never returned by any endpoint —
it remains internal processing state.

**Task 3C adds the frontend document-management experience.** The
frontend now has a protected `/documents` route (unauthenticated
visitors are redirected to `/login`) with a full document list,
upload, download, delete, and an explicit "Process" action wired to
the endpoint above — all built on the existing backend contract with
no new backend routes.

**`/process` now also chunks extracted text.** After parsing, text is
split into deterministic, ordered chunks (`document_chunks` table)
and persisted atomically alongside the extracted-text row — both
succeed together or neither is durably changed.

**`/process` now also generates embeddings for each chunk.** Every
chunk's text is embedded via OpenAI `text-embedding-3-small` (1536
dimensions) and persisted on `document_chunks.embedding` (pgvector),
still inside the same atomic transaction as the extracted text and
chunks — a document's text, chunks, and embeddings all become durable
together or not at all, even if the embedding provider fails.
Processing is synchronous — no background worker, no Redis. Chunks
and embeddings remain entirely internal processing state, same as
extracted text: no endpoint exposes them, and no frontend UI shows
them. No vector index exists yet (deferred until multi-document
retrieval actually needs one).

**A new internal `retrieval_service.retrieve_similar_chunks()`
primitive can now perform pgvector cosine-distance similarity search**
over a single, already-authorized document's chunks — the first real
consumer of the stored embeddings. It's not exposed via any HTTP
endpoint yet; it exists as a tested building block for a future
RAG/chat layer. RAG and chat themselves are not implemented. See
`docs/PROJECT_CONTEXT.md` for full current state and `CHANGELOG.md`
for the task-by-task history.

## Stack

- **Backend:** FastAPI, SQLAlchemy (async), Alembic, PostgreSQL + pgvector
- **Frontend:** React, TypeScript, Vite
- **Local dev:** Docker Compose

## Running locally

### Option A — Docker Compose (recommended)

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:5173
- Postgres: localhost:5432
- Uploaded documents persist in the `uploads_data` Docker volume

### Option B — run services individually

**Backend:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # edit DATABASE_URL if not using Docker for postgres
python -m uvicorn app.main:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## Verifying it's working

- `GET http://localhost:8000/api/v1/health` should return
  `{"status": "ok", ...}` once Postgres is reachable.
- The frontend root page calls this same endpoint and displays the
  result — a quick end-to-end smoke test.

## Database migrations

```bash
cd backend
python -m alembic revision --autogenerate -m "description"
python -m alembic upgrade head
```

## Project structure

See `docs/ARCHITECTURE.md` for the full rationale. Top level:

```
researchpilot/
├── backend/    # FastAPI app
├── frontend/   # React + Vite app
├── docs/       # architecture, API, roadmap docs
└── docker-compose.yml
```
