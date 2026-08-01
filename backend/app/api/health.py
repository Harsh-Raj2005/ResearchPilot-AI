"""
Health check endpoint.

Deliberately checks a real DB round trip (SELECT 1), not just "the
process is alive" — a health check that can't tell you the database
is unreachable isn't useful in production. If the DB is down this
returns a 503 so a deploy platform's health probe fails correctly
instead of reporting false-healthy.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001 — deliberately broad: any DB failure means unhealthy
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "database": "unreachable", "error": str(exc)},
        ) from exc

    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "database": db_status,
    }
