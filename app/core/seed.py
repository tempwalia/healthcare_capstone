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
    ("audit:view", "Read the audit log"),
    ("analytics:view", "View care-coordination analytics/reporting"),
    ("admin:*", "Unrestricted access — bypasses all other permission checks"),
]

ROLE_PERMISSIONS = {
    "patient": ["referral:create", "referral:view_own"],
    "pcp": ["referral:create", "referral:view_own"],
    "specialist": ["referral:view_own"],
    "care_coordinator": [
        "referral:view_all", "referral:approve", "referral:override",
        "referral:record_outcome", "analytics:view",
    ],
    "payer_admin": ["referral:view_all", "analytics:view"],
    "admin": ["admin:*"],
}

ROLE_DESCRIPTIONS = {
    "patient": "Patient portal account",
    "pcp": "Primary care provider — refers patients to specialists",
    "specialist": "Specialist physician — receives and acts on referrals",
    "care_coordinator": "Care coordination staff — approves/escalates referral workflow steps",
    "payer_admin": "Payer-side read access to eligibility and analytics",
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
