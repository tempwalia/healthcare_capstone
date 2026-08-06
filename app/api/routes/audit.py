from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogResponse
from app.schemas.common import Page

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/", response_model=Page[AuditLogResponse])
async def list_audit_log(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("audit:view")),
):
    total = (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.id.desc()).offset(skip).limit(limit)
    )
    return build_page(request, result.scalars().all(), total, skip, limit)
