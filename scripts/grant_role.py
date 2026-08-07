"""Dev/manual-testing utility: there is no API endpoint for role assignment
(by design — see CAPSTONE_IMPLEMENTATION_GUIDE.md's RBAC notes), so grant a
role to an already-registered user directly.

    uv run python scripts/grant_role.py <username> <role_name>

Valid role_name values: patient, pcp, specialist, care_coordinator,
payer_admin, doctor, admin (see app/core/seed.py's ROLE_PERMISSIONS).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.core.seed import seed_roles_and_permissions  # noqa: E402
from app.database.session import async_session  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.models.user import User  # noqa: E402


async def main(username: str, role_name: str) -> None:
    async with async_session() as db:
        await seed_roles_and_permissions(db)

        user = (
            await db.execute(
                select(User).options(selectinload(User.roles)).where(User.username == username)
            )
        ).scalar_one()
        role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one()

        if role not in user.roles:
            user.roles.append(role)
            await db.commit()
            print(f"Granted '{role_name}' to '{username}'.")
        else:
            print(f"'{username}' already has '{role_name}'.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: uv run python scripts/grant_role.py <username> <role_name>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
