# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] — Phase 1

### Task 1 — Project skeleton
- Initialized monorepo structure (`backend/`, `frontend/`, `docs/`).
- Backend: FastAPI app factory, Pydantic Settings config, async
  SQLAlchemy engine/session, Alembic configured for async migrations,
  `GET /api/v1/health` endpoint (real DB round-trip check).
- Frontend: React + Vite + TypeScript scaffold, minimal API client,
  health-check verification screen.
- Docker Compose (backend + frontend + postgres/pgvector) for local dev.
- Root `.gitignore`, `README.md`, `.env.example` for both services.
- Verified: backend installs and boots via uvicorn, `/docs` and
  `/api/v1/health` respond correctly (503 when DB unreachable, as
  designed); frontend installs, type-checks, builds, and serves via
  `npm run dev`.
