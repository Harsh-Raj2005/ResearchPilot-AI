# ResearchPilot AI

An AI-powered research assistant — upload papers, chat with them, and
(in later phases) compare, summarize, and reason across a whole
literature set.

Built incrementally, one phase and one task at a time. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the phase plan and
[`docs/API.md`](docs/API.md) for API documentation.

## Current status

**Phase 1, Task 3B complete (all four checkpoints): authenticated
document upload works end-to-end, with local storage now persisted
via a Docker volume in dev.** `POST /api/v1/documents/upload` composes
the `documents` table, the local-disk storage service, and
`get_current_user`. **Document listing, detail, download, and delete
are not implemented** — that scope was explicitly excluded from every
Task 3B checkpoint and remains unscheduled. See
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
