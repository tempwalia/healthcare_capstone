from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_active_user
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.notification import Notification
from app.models.user import User
from app.schemas.common import Page
from app.schemas.notification import NotificationResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Self-scoped by construction (WHERE user_id == caller), same shape as
# GET /auth/me — no permission needed, every authenticated user has exactly
# one notification inbox: their own.


@router.get("/", response_model=Page[NotificationResponse])
async def list_notifications(
    request: Request,
    skip: int = 0,
    limit: int = 20,
    unread_only: Optional[bool] = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    base_query = select(Notification).where(Notification.user_id == current_user.id)
    if unread_only:
        base_query = base_query.where(Notification.read_at.is_(None))

    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.order_by(Notification.created_at.desc()).offset(skip).limit(limit))
    return build_page(request, result.scalars().all(), total, skip, limit)


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    notification = (
        await db.execute(
            select(Notification).where(Notification.id == notification_id, Notification.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if not notification:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")

    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(notification)
    return notification
