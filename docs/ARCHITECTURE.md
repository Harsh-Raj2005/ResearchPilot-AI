# Architecture

See the Phase 0 planning document for full rationale on every
technology choice (why FastAPI, why pgvector over Qdrant for now,
why no LangChain, etc.).

## Production deployment architecture (current — live)

ResearchPilot AI is deployed and running in production:

- **Frontend:** https://research-pilot-ai-ashy.vercel.app/
- **Backend:** https://researchpilot-ai-jccb.onrender.com/

```
Browser
  │
  ▼
Vercel (React + TypeScript + Vite, static build)
  │ HTTPS
  ▼
Render (FastAPI, app.main:app)
  │
  ├──► Neon (PostgreSQL + pgvector)
  │
  ├──► Cloudflare R2 (uploaded documents, private bucket)
  │
  └──► OpenAI (embeddings + LLM generation)
```

- **Vercel** is the production frontend — the static React + TypeScript
  + Vite build.
- **Render** hosts the FastAPI backend (`app.main:app`).
- **Neon** provides PostgreSQL + pgvector.
- **Cloudflare R2** provides private document storage.
- **OpenAI** provides embeddings and LLM generation.
- **The R2 bucket remains private.** There are no public object URLs
  and no presigned-URL redirects; the frontend never talks to R2
  directly.
- **The backend remains responsible for authenticated document
  access** — every file's bytes are proxied through its own
  ownership-checked endpoints, exactly as they were when storage was
  local disk.

`frontend/vercel.json` supplies a single SPA rewrite so client-side
routes (`/login`, `/signup`, `/documents`,
`/documents/:documentId/chat`) load on direct navigation or refresh.
That is a Vercel static-hosting rule, not a backend route — it adds no
API surface.

## Historical — why this architecture was chosen (pre-deployment research)

*Kept for rationale. This described a target at the time; it is now the
architecture actually running above.*

Chosen specifically to guarantee a genuine $0/month mandatory hosting
cost — Railway and Render's own free Postgres were both researched
and explicitly ruled out (Railway no longer has a permanent free
tier; Render's free Postgres expires 30 days after creation).
Free-tier limits and pricing should be re-verified against each
provider's current documentation over time, since free tiers change.
OpenAI usage is separate from hosting and remains pay-as-you-go.

## Historical (Task 1) architecture

```
frontend (React/Vite) ──HTTP──> backend (FastAPI) ──> Postgres
```

Redis, background workers, and the LLM integration are not part of
the stack yet — they're introduced in the tasks that need them
(document processing, chat), not speculatively.

## Layering (backend)

`app/api/` (routing, validation) → `app/services/` (business logic) →
`app/models/` (DB access). Routers never touch the DB directly.
