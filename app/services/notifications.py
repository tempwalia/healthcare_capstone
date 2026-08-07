from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


async def create_notification(
    db: AsyncSession, *, user_id: int, title: str, body: Optional[str] = None, referral_id: Optional[int] = None
) -> None:
    """Adds a Notification without committing — rides the caller's own
    transaction, same convention as log_action/write_outbox_event."""
    db.add(Notification(user_id=user_id, title=title, body=body, referral_id=referral_id))
