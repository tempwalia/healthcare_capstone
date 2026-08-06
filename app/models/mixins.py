from sqlalchemy import Column, DateTime


class SoftDeleteMixin:
    """Adds deleted_at instead of hard-deleting rows. Queries must filter
    `deleted_at.is_(None)` explicitly — see app/api/dependencies/pagination.py (Phase 2)."""

    deleted_at = Column(DateTime(timezone=True), nullable=True)
