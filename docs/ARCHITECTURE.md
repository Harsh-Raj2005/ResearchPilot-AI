# Architecture

See the Phase 0 planning document for full rationale on every
technology choice (why FastAPI, why pgvector over Qdrant for now,
why no LangChain, etc.).

## Current (Task 1) architecture

```
frontend (React/Vite) ──HTTP──> backend (FastAPI) ──> Postgres
```

Redis, background workers, and the LLM integration are not part of
the stack yet — they're introduced in the tasks that need them
(document processing, chat), not speculatively.

## Layering (backend)

`app/api/` (routing, validation) → `app/services/` (business logic) →
`app/models/` (DB access). Routers never touch the DB directly.
