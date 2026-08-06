from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import false

from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.referral import ReferralRequest
from app.models.role import Role
from app.models.user import User


async def referral_visibility_filter(db: AsyncSession, current_user: User):
    """Returns a SQLAlchemy filter expression to AND onto a ReferralRequest
    query, or None for "no restriction" (referral:view_all / admin:*).

    This is the concrete fix for the Known Gap "no role-based scoping — every
    authenticated user can see every patient's referrals": a patient sees only
    their own referral, a PCP sees referrals they created, a specialist sees
    referrals where they're the assigned specialist, and a care coordinator
    (or admin) sees everything.
    """
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == current_user.id)
    )
    user = result.scalar_one()
    granted = {p.name for role in user.roles for p in role.permissions}

    if "referral:view_all" in granted or "admin:*" in granted:
        return None

    if "referral:view_own" not in granted:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Missing required permission: referral:view_own or referral:view_all",
        )

    patient = (await db.execute(select(Patient).where(Patient.user_id == current_user.id))).scalar_one_or_none()
    doctor = (await db.execute(select(Doctor).where(Doctor.user_id == current_user.id))).scalar_one_or_none()

    conditions = []
    if patient:
        conditions.append(ReferralRequest.patient_id == patient.id)
    if doctor:
        conditions.append(ReferralRequest.referring_doctor_id == doctor.id)
        conditions.append(ReferralRequest.specialist_id == doctor.id)

    if not conditions:
        # Authenticated, holds referral:view_own, but isn't linked to any
        # patient/doctor record yet — sees nothing rather than everything.
        return false()

    return or_(*conditions)
