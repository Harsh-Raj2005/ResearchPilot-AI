# Architecture

See the Phase 0 planning document for full rationale on every
technology choice (why FastAPI, why pgvector over Qdrant for now,
why no LangChain, etc.).

## Deployment target architecture

Not yet deployed — this describes the researched, approved target,
prepared for by the R2 storage migration and this deployment-readiness
pass. Actual provisioning (creating the Render/Vercel/Neon/Cloudflare
accounts, setting environment variables, running the production
migration) is a separate, not-yet-performed step.

```
Browser
  │
  ▼
Vercel (React/Vite, static build)          FREE
  │ HTTPS
  ▼
Render (FastAPI, app.main:app)             FREE — sleeps after 15 min idle
  │
  ├──► Neon (PostgreSQL + pgvector)        FREE — scales to zero after 5 min idle
  │
  ├──► Cloudflare R2 (uploaded PDFs)       FREE — 10GB, no time limit
  │
  └──► OpenAI API (embeddings + LLM)       pay-as-you-go, separate from hosting
```

Chosen specifically to guarantee a genuine $0/month mandatory hosting
cost — Railway and Render's own free Postgres were both researched
and explicitly ruled out (Railway no longer has a permanent free
tier; Render's free Postgres expires 30 days after creation). All
free-tier limits and pricing should be re-verified against each
provider's current documentation at actual deploy time, since free
tiers change.

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
