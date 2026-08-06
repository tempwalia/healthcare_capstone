"""Idempotent role/permission seeding. Run after every migration:
    uv run python scripts/seed_roles.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.seed import seed_roles_and_permissions  # noqa: E402
from app.database.session import async_session  # noqa: E402


async def main() -> None:
    async with async_session() as db:
        await seed_roles_and_permissions(db)
    print("Roles and permissions seeded.")


if __name__ == "__main__":
    asyncio.run(main())
