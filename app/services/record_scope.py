from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import false

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.patient import Patient
from app.models.role import Role
from app.models.user import User


async def _granted_permissions(db: AsyncSession, current_user: User) -> set[str]:
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == current_user.id)
    )
    user = result.scalar_one()
    return {p.name for role in user.roles for p in role.permissions}


async def _own_patient_and_doctor(db: AsyncSession, current_user: User):
    patient = (
        await db.execute(select(Patient).where(Patient.user_id == current_user.id))
    ).scalar_one_or_none()
    doctor = (
        await db.execute(select(Doctor).where(Doctor.user_id == current_user.id))
    ).scalar_one_or_none()
    return patient, doctor


async def patient_visibility_filter(db: AsyncSession, current_user: User):
    """Returns a SQLAlchemy filter to AND onto a Patient query, or None for
    "no restriction". Unlike appointment/medical-record visibility below,
    `patient:view_all` is granted broadly to every clinical/coordination
    role (pcp, specialist, care_coordinator) rather than scoped to a care
    team — this app has no patient-panel/assignment model, so any provider
    may legitimately need to pull up any patient's chart (new intake,
    walk-in, cross-coverage), same trade-off as `referral_scope.py` makes
    for care_coordinator's `referral:view_all`. The `patient` role itself
    stays confined to its own linked record."""
    granted = await _granted_permissions(db, current_user)
    if "patient:view_all" in granted or "admin:*" in granted:
        return None

    if "patient:view_own" not in granted:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Missing required permission: patient:view_own or patient:view_all",
        )

    patient, _ = await _own_patient_and_doctor(db, current_user)
    if not patient:
        return false()
    return Patient.id == patient.id


async def appointment_visibility_filter(db: AsyncSession, current_user: User):
    """Stays need-to-know even for clinical roles: `appointment:view_own` is
    scoped to encounters the caller is actually party to (as the patient or
    the assigned doctor); only `appointment:view_all` (care coordination)
    sees every appointment."""
    granted = await _granted_permissions(db, current_user)
    if "appointment:view_all" in granted or "admin:*" in granted:
        return None

    if "appointment:view_own" not in granted:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Missing required permission: appointment:view_own or appointment:view_all",
        )

    patient, doctor = await _own_patient_and_doctor(db, current_user)
    conditions = []
    if patient:
        conditions.append(Appointment.patient_id == patient.id)
    if doctor:
        conditions.append(Appointment.doctor_id == doctor.id)

    if not conditions:
        return false()
    return or_(*conditions)


async def medical_record_visibility_filter(db: AsyncSession, current_user: User):
    """Same need-to-know shape as `appointment_visibility_filter` — clinical
    notes are more sensitive than the patient directory, so even pcp/
    specialist only see records they're a party to; care_coordinator holds
    `medical_record:view_all` for oversight but not `:manage` (coordinators
    don't author clinical notes)."""
    granted = await _granted_permissions(db, current_user)
    if "medical_record:view_all" in granted or "admin:*" in granted:
        return None

    if "medical_record:view_own" not in granted:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Missing required permission: medical_record:view_own or medical_record:view_all",
        )

    patient, doctor = await _own_patient_and_doctor(db, current_user)
    conditions = []
    if patient:
        conditions.append(MedicalRecord.patient_id == patient.id)
    if doctor:
        conditions.append(MedicalRecord.doctor_id == doctor.id)

    if not conditions:
        return false()
    return or_(*conditions)
