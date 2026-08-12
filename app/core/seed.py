from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Permission, Role

PERMISSIONS = [
    ("referral:create", "Submit a new referral"),
    ("referral:view_own", "View referrals the user is a party to (own patient/PCP/specialist record)"),
    ("referral:view_all", "View every referral in the platform"),
    ("referral:approve", "Approve a human-in-the-loop workflow decision (specialist selection, eligibility escalation)"),
    ("referral:override", "Override an automated referral decision"),
    ("referral:record_outcome", "Record a specialist consult outcome (symptoms, diagnosis, prescription) once a referral appointment has occurred"),
    ("patient:view_own", "View your own patient record"),
    ("patient:view_all", "View any patient record (clinical/coordination directory lookup)"),
    ("patient:manage", "Create, update, or delete patient records"),
    ("doctor:manage", "Create, update, or delete doctor directory records"),
    ("appointment:view_own", "View appointments you are a party to, as the patient or the assigned doctor"),
    ("appointment:view_all", "View every appointment in the platform"),
    ("appointment:manage", "Create, update, or delete appointments"),
    ("medical_record:view_own", "View medical records you are a party to, as the patient or the assigned doctor"),
    ("medical_record:view_all", "View every medical record in the platform"),
    ("medical_record:manage", "Create, update, or delete medical records"),
    ("audit:view", "Read the audit log"),
    ("analytics:view", "View care-coordination analytics/reporting"),
    ("admin:*", "Unrestricted access — bypasses all other permission checks"),
]

ROLE_PERMISSIONS = {
    "patient": [
        "referral:create", "referral:view_own",
        "patient:view_own", "appointment:view_own",
        "medical_record:view_own", "medical_record:manage",
    ],
    "pcp": [
        "referral:create", "referral:view_own",
        "patient:view_all", "patient:manage", "doctor:manage",
        "appointment:view_own", "appointment:manage",
        "medical_record:view_own", "medical_record:manage",
    ],
    "specialist": [
        "referral:view_own", "referral:approve", "referral:record_outcome",
        "patient:view_all", "patient:manage", "doctor:manage",
        "appointment:view_own", "appointment:manage",
        "medical_record:view_own", "medical_record:manage",
    ],
    "care_coordinator": [
        "referral:create", "referral:view_all", "referral:approve", "referral:override",
        "referral:record_outcome", "analytics:view",
        "patient:view_all", "patient:manage", "doctor:manage",
        "appointment:view_all", "appointment:manage",
        "medical_record:view_all", "medical_record:manage",
    ],
    "payer_admin": ["referral:view_all", "analytics:view"],
    "doctor": [
        "referral:view_all", "referral:approve", "referral:record_outcome",
        "patient:view_all", "appointment:view_all",
        "medical_record:view_all", "medical_record:manage",
    ],
    "admin": ["admin:*"],
}

ROLE_DESCRIPTIONS = {
    "patient": "Patient portal account",
    "pcp": "Primary care provider — refers patients to specialists",
    "specialist": "Specialist physician — receives and acts on referrals",
    "care_coordinator": "Care coordination staff — approves/escalates referral workflow steps",
    "payer_admin": "Payer-side read access to eligibility and analytics",
    "doctor": (
        "POC stand-in for a specialist actually seeing patients: not tied to any one referral, so it can "
        "pick up and complete any referral platform-wide — select a specialist, then record the consult "
        "outcome/prescription that closes it out"
    ),
    "admin": "Platform administrator",
}


async def seed_roles_and_permissions(db: AsyncSession) -> None:
    """Idempotent: safe to run on every startup. Creates any missing roles/
    permissions and makes sure each role has exactly the permission set above."""

    permissions_by_name: dict[str, Permission] = {}
    for name, description in PERMISSIONS:
        result = await db.execute(select(Permission).where(Permission.name == name))
        permission = result.scalar_one_or_none()
        if permission is None:
            permission = Permission(name=name, description=description)
            db.add(permission)
            await db.flush()
        permissions_by_name[name] = permission

    for role_name, permission_names in ROLE_PERMISSIONS.items():
        result = await db.execute(
            select(Role)
            .where(Role.name == role_name)
        )
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(name=role_name, description=ROLE_DESCRIPTIONS[role_name])
            db.add(role)
            await db.flush()

        # refresh with permissions loaded so membership checks below see current state
        await db.refresh(role, attribute_names=["permissions"])
        existing = {p.name for p in role.permissions}
        for name in permission_names:
            if name not in existing:
                role.permissions.append(permissions_by_name[name])

    await db.commit()
