from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notification import Notification
from app.models.role import Role


async def create_notification(
    db: AsyncSession, *, user_id: int, title: str, body: Optional[str] = None, referral_id: Optional[int] = None
) -> None:
    """Adds a Notification without committing — rides the caller's own
    transaction, same convention as log_action/write_outbox_event."""
    db.add(Notification(user_id=user_id, title=title, body=body, referral_id=referral_id))


async def create_notification_for_role(
    db: AsyncSession, *, role_name: str, title: str, body: Optional[str] = None, referral_id: Optional[int] = None
) -> int:
    """Same no-commit contract as create_notification, broadcast to every
    user holding `role_name`. There's no per-referral "assigned coordinator"
    concept in this data model — referral:approve is a role permission, not
    an assignment — so a role-wide broadcast is the only way to give this
    kind of event a real addressee. Returns how many users were notified."""
    role = (
        await db.execute(select(Role).options(selectinload(Role.users)).where(Role.name == role_name))
    ).scalar_one_or_none()
    if role is None:
        return 0
    for user in role.users:
        await create_notification(db, user_id=user.id, title=title, body=body, referral_id=referral_id)
    return len(role.users)
