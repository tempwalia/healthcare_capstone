import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.database import get_async_session
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness():
    """Process is up and serving requests. Does not touch the database —
    a slow/unreachable DB should surface as not-ready, not not-alive."""
    return {"status": "alive", "version": settings.version}


@router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_async_session)):
    """Actually pings the database, unlike a plain '/health' that would return
    healthy even with Postgres down (REBUILD_GUIDE Known Gap)."""
    try:
        await db.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        logger.exception("readiness check: database ping failed")
        database_ok = False

    healthy = database_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "healthy" if healthy else "degraded", "database": database_ok},
    )
