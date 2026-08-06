import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import session as db_session
from app.events import broadcaster
from app.models.outbox import OutboxEvent

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 0.5
BATCH_SIZE = 50


async def process_pending_events() -> int:
    """Publishes any unpublished outbox rows and marks them published. Split
    out from the infinite loop below so it's directly unit-testable, and so
    the loop itself has no logic beyond "call this, then sleep"."""
    published = 0
    async with db_session.async_session() as db:
        result = await db.execute(
            select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).limit(BATCH_SIZE)
        )
        rows = result.scalars().all()
        if not rows:
            return 0

        for row in rows:
            if row.referral_id is not None:
                broadcaster.publish(row.referral_id, row.payload)
            row.published_at = datetime.now(timezone.utc)
            published += 1

        await db.commit()
    return published


async def publish_loop() -> None:
    """Runs for the life of the process (started from app/main.py's lifespan).
    Never runs during tests — ASGITransport doesn't trigger FastAPI lifespan
    events, so a background poller here can't race against test teardown."""
    while True:
        try:
            await process_pending_events()
        except Exception:
            logger.exception("outbox publisher: error processing pending events")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
