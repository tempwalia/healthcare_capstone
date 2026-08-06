from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.database.base import Base


class OutboxEvent(Base):
    """Transactional outbox: written in the same DB transaction as the state
    change that caused it (never a separate commit), so there's no dual-write
    problem between "the state changed" and "an event says it changed". A
    background publisher (app/events/publisher.py) polls unpublished rows and
    fans them out — currently to an in-process broadcaster (see
    app/events/broadcaster.py); swap that one function for Redis pub/sub if
    this ever needs to fan out across more than one process."""

    __tablename__ = "outbox_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(100), nullable=False)
    referral_id = Column(Integer, nullable=True, index=True)
    payload = Column(Text, nullable=False)  # JSON-encoded
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    published_at = Column(DateTime(timezone=True), nullable=True)
