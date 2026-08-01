# API Documentation

Base URL: `/api/v1`

## Health

### `GET /health`
Returns application and database status.

**Response `200`**
```json
{ "status": "ok", "app": "ResearchPilot AI", "environment": "development", "database": "ok" }
```

**Response `503`** (database unreachable)
```json
{ "detail": { "status": "error", "database": "unreachable", "error": "..." } }
```

---

_Auth, document, and chat endpoints are documented here as each is
implemented — see the Phase 1 Technical Design Document for the full
planned surface._
