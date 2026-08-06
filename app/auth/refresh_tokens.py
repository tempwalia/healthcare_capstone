import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.time_utils import ensure_aware
from app.models.refresh_token import RefreshToken


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def is_expired(expires_at: datetime) -> bool:
    return ensure_aware(expires_at) < datetime.now(timezone.utc)


async def issue_refresh_token(db: AsyncSession, user_id: int) -> str:
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    return raw


async def rotate_refresh_token(db: AsyncSession, stored: RefreshToken) -> str:
    """Revokes `stored` and issues its replacement. Rotation-on-use means a
    stolen token that gets used by both an attacker and the legitimate client
    produces a detectable double-use — the second use hits an already-revoked
    row, which callers should treat as a signal worth auditing."""
    stored.revoked_at = datetime.now(timezone.utc)
    raw = secrets.token_urlsafe(48)
    new_row = RefreshToken(
        user_id=stored.user_id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(new_row)
    await db.flush()
    stored.replaced_by_id = new_row.id
    return raw
