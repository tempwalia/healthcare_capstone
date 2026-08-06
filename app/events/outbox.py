import json
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxEvent


async def write_outbox_event(
    db: AsyncSession, event_type: str, payload: dict, referral_id: Optional[int] = None
) -> None:
    """Adds an OutboxEvent without committing — call this alongside a state
    change and let the caller's own db.commit() cover both atomically."""
    db.add(
        OutboxEvent(
            event_type=event_type,
            referral_id=referral_id,
            payload=json.dumps(payload, default=str),
        )
    )
