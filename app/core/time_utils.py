from datetime import datetime, timezone


def ensure_aware(value: datetime) -> datetime:
    """SQLite round-trips DateTime(timezone=True) values as naive (unlike
    Postgres, where asyncpg preserves tzinfo) — any datetime read back from
    the DB and then compared against `datetime.now(timezone.utc)` or another
    DB-read value needs this, or the comparison silently misbehaves: `<`
    raises TypeError, but `==`/`in` between aware and naive datetimes just
    returns False instead of erroring, which is the more dangerous failure
    mode (see the ScheduleSlot idempotency bug this was extracted from).
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
