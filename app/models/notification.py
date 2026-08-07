from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database.base import Base


class Notification(Base):
    """A user-facing, in-app notification — separate from the mock external
    send_notification MCP tool (mock_systems/notification_mock), which stands
    in for a real email/SMS/push provider and never persists anything. This
    is what the dashboard's notification bell actually reads."""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)
    referral_id = Column(Integer, ForeignKey("referral_requests.id"), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
