from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.database import get_async_session
from app.auth.jwt_handler import verify_token
from app.models.role import Role
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_session),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    username = verify_token(token)
    if username is None:
        raise credentials_exception

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def require_permission(permission: str):
    """Dependency factory gating a route behind a named permission, checked
    against the DB-driven Role/Permission tables (not a hardcoded role string).
    A role holding the `admin:*` permission bypasses every check."""

    async def _dependency(
        current_user: User = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_async_session),
    ) -> User:
        result = await db.execute(
            select(User)
            .options(selectinload(User.roles).selectinload(Role.permissions))
            .where(User.id == current_user.id)
        )
        user = result.scalar_one()
        granted = {p.name for role in user.roles for p in role.permissions}

        if permission not in granted and "admin:*" not in granted:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Missing required permission: {permission}"
            )
        return current_user

    return _dependency
