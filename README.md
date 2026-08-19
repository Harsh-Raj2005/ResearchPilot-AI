# ResearchPilot AI

An AI-powered research assistant — upload papers, chat with them, and
(in later phases) compare, summarize, and reason across a whole
literature set.

Built incrementally, one phase and one task at a time. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the phase plan and
[`docs/API.md`](docs/API.md) for API documentation.

## Current status

**ResearchPilot AI is deployed and live.**

- **Frontend (Vercel):** https://research-pilot-ai-ashy.vercel.app/
- **Backend (Render):** https://researchpilot-ai-jccb.onrender.com/

Phase 1 — *single-document upload + chat, deployed* — has reached its
target end state. Signup/login, document upload, explicit processing
(text extraction → chunking → embeddings), single-document RAG, and
persistent chat all work in production, on Neon PostgreSQL + pgvector,
Cloudflare R2 (private bucket), and OpenAI.

The milestone notes below are a running history of what each step
added. They describe what was true when each was written, not the
current deployment state — for that, see `docs/ARCHITECTURE.md` and
`docs/PROJECT_CONTEXT.md`.

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
endpoint yet.

**A new internal `rag_service.answer_question()` primitive now
connects retrieval to an LLM** — it embeds a question, retrieves the
most relevant chunks from one authorized document, assembles a
grounded prompt (retrieved text is treated as untrusted reference
material, not instructions), and calls a new `llm_service.py`
(OpenAI Chat Completions) to generate an answer. If no relevant
chunks are found, it returns a deterministic fallback without calling
the LLM at all.

**`POST /api/v1/documents/{document_id}/chat` now exposes that
pipeline over HTTP.** Authenticated, ownership-checked via the
existing pattern, stateless (no conversation history, no session) —
one question in, `{"answer": "..."}` out. The router treats
`rag_service` as a black box: no embedding, retrieval, prompt
construction, or LLM logic lives in the endpoint itself.

**Conversations now persist.** New `ChatSession`/`ChatMessage`
tables back four new endpoints — create a session, list sessions,
send a message, and retrieve history — all nested under
`/documents/{document_id}/chat/sessions...` and scoped by both
document and session ownership. Sending a message persists the
user's question and the assistant's reply atomically (one commit,
only after the LLM call succeeds), and passes the session's recent
history (up to the last 10 messages) into the LLM via a native
multi-turn message list, so follow-up questions are actually
answered with conversational context. The original stateless
`POST /documents/{document_id}/chat` endpoint is unchanged and
still available.

**A frontend chat UI now consumes all of this.** A protected
`/documents/:documentId/chat` route lets a signed-in user open a
document's conversation workspace: a session sidebar (create new,
switch between existing), a message view distinguishing user and
assistant turns, and a composer (Enter to send, Shift+Enter for a
newline). The server remains the sole source of truth — there is no
client-side conversation store, so a page refresh, logout/login, or
switching sessions always reloads the real persisted transcript from
the API. `401` reuses the existing logout-and-redirect handling;
`404`/`422`/`502` are shown as plain, non-technical messages. See
`docs/PROJECT_CONTEXT.md` for full current state and `CHANGELOG.md`
for the task-by-task history.

**Uploaded documents live in Cloudflare R2, not local disk.**
Local disk on a typical free-tier hosting container isn't durable
across redeploys or restarts, so `storage_service.py` was migrated to
async R2 access (via `aioboto3`). The bucket is private — the backend
proxies every file's bytes through its own authenticated endpoints,
and the frontend never talks to R2 directly.

**That deployment has since happened.** The frontend runs on Vercel,
the FastAPI backend on Render, and the database is Neon PostgreSQL +
pgvector, with Cloudflare R2 for document storage and OpenAI for
embeddings and generation. See `docs/ARCHITECTURE.md` for the
production architecture diagram.

**Latest change — production SPA routing and visible logout.**
`frontend/vercel.json` adds a rewrite so every client-side route
(`/login`, `/signup`, `/documents`, `/documents/:documentId/chat`)
serves the SPA entry point on direct navigation or refresh instead of
a Vercel 404. The Documents and Chat workspace headers each show a
"Log out" button that reuses the existing `AuthContext` logout and
redirects to `/login`. Frontend-only — no backend, API, or database
change.

## Stack

- **Backend:** FastAPI, SQLAlchemy (async), Alembic, PostgreSQL + pgvector
- **Storage:** Cloudflare R2 (S3-compatible object storage) for uploaded documents
- **AI:** OpenAI — `text-embedding-3-small` embeddings + chat-completions generation
- **Frontend:** React, TypeScript, Vite
- **Production hosting:** Vercel (frontend), Render (FastAPI backend), Neon (PostgreSQL + pgvector)
- **Local dev:** Docker Compose

## Running locally

These instructions are for **local development**. Production is a
separate environment — Vercel + Render + Neon, deployed from `main`,
not from Docker Compose. Credentials for either environment live only
in environment variables and are never committed.

### Option A — Docker Compose (recommended)

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:5173
- Postgres: localhost:5432
- Uploaded documents are stored in Cloudflare R2 — fill in the four
  `R2_*` values in `backend/.env` with a real bucket's credentials to
  exercise upload/process/download locally (automated tests never
  need this; storage is always mocked in `pytest`)

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
