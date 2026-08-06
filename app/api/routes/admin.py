from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import require_permission
from app.api.dependencies.database import get_async_session
from app.core.security import get_password_hash
from app.core.seed import ROLE_PERMISSIONS
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.refresh_token import RefreshToken
from app.models.role import Role
from app.models.user import User
from app.schemas.admin import PasswordResetRequest, RoleAssignmentRequest, UserWithRoles

router = APIRouter(prefix="/admin", tags=["admin"])


def _to_user_with_roles(user: User) -> UserWithRoles:
    return UserWithRoles(
        id=user.id, username=user.username, email=user.email,
        is_active=user.is_active, roles=sorted(r.name for r in user.roles),
    )


async def _get_user_with_roles(db: AsyncSession, user_id: int) -> User:
    user = (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user_id)
        )
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/roles", response_model=list[str])
async def list_role_names(current_user: User = Depends(require_permission("admin:*"))):
    return sorted(ROLE_PERMISSIONS.keys())


@router.get("/users", response_model=list[UserWithRoles])
async def list_users(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("admin:*")),
):
    result = await db.execute(select(User).options(selectinload(User.roles)).order_by(User.id))
    return [_to_user_with_roles(u) for u in result.scalars().all()]


@router.post("/users/{user_id}/roles", response_model=UserWithRoles)
async def grant_role(
    user_id: int,
    data: RoleAssignmentRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("admin:*")),
):
    user = await _get_user_with_roles(db, user_id)
    role = (await db.execute(select(Role).where(Role.name == data.role_name))).scalar_one_or_none()
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown role: {data.role_name}")

    if role not in user.roles:
        user.roles.append(role)
        await db.commit()
        await db.refresh(user, attribute_names=["roles"])
    return _to_user_with_roles(user)


@router.delete("/users/{user_id}/roles/{role_name}", response_model=UserWithRoles)
async def revoke_role(
    user_id: int,
    role_name: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("admin:*")),
):
    user = await _get_user_with_roles(db, user_id)
    user.roles = [r for r in user.roles if r.name != role_name]
    await db.commit()
    await db.refresh(user, attribute_names=["roles"])
    return _to_user_with_roles(user)


@router.post("/users/{user_id}/reset-password", response_model=UserWithRoles)
async def reset_password(
    user_id: int,
    data: PasswordResetRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("admin:*")),
):
    """Admins can set a new password for a user — never read the existing
    one back. Passwords are stored as a one-way bcrypt hash
    (`app/core/security.py`); that's not a policy choice an admin flag can
    override, it's what makes "hashed" mean anything at all. Resetting also
    revokes the user's existing refresh tokens so old sessions can't outlive
    a credential reset."""
    user = await _get_user_with_roles(db, user_id)
    user.hashed_password = get_password_hash(data.new_password)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    )
    now = datetime.now(timezone.utc)
    for token in result.scalars().all():
        token.revoked_at = now

    await db.commit()
    return _to_user_with_roles(user)


@router.post("/users/{user_id}/link-patient/{patient_id}", response_model=UserWithRoles)
async def link_patient(
    user_id: int,
    patient_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("admin:*")),
):
    user = await _get_user_with_roles(db, user_id)
    patient = await db.get(Patient, patient_id)
    if not patient or patient.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    if patient.user_id is not None and patient.user_id != user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Patient is already linked to a different user")

    patient.user_id = user_id
    await db.commit()
    return _to_user_with_roles(user)


@router.post("/users/{user_id}/link-doctor/{doctor_id}", response_model=UserWithRoles)
async def link_doctor(
    user_id: int,
    doctor_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("admin:*")),
):
    user = await _get_user_with_roles(db, user_id)
    doctor = await db.get(Doctor, doctor_id)
    if not doctor or doctor.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")
    if doctor.user_id is not None and doctor.user_id != user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Doctor is already linked to a different user")

    doctor.user_id = user_id
    await db.commit()
    return _to_user_with_roles(user)
