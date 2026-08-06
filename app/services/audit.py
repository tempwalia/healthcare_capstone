import json
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def log_action(
    db: AsyncSession,
    *,
    actor_id: Optional[int],
    action: str,
    resource_type: str,
    resource_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> None:
    """Adds an AuditLog row to the current session without committing — rides
    along in the same transaction as the caller's own state change, so an audit
    entry never exists for a write that didn't actually happen (and vice versa)."""
    db.add(
        AuditLog(
            user_id=actor_id,
            action=action,
            details=json.dumps(
                {"resource_type": resource_type, "resource_id": resource_id, **(details or {})}
            ),
        )
    )
