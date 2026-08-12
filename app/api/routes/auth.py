from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import get_current_active_user
from app.api.dependencies.database import get_async_session
from app.auth.jwt_handler import create_access_token
from app.auth.refresh_tokens import hash_token, is_expired, issue_refresh_token, rotate_refresh_token
from app.core.rate_limit import limiter
from app.core.security import get_password_hash, verify_password
from app.core.seed_data import random_patient_profile
from app.models.patient import Patient
from app.models.refresh_token import RefreshToken
from app.models.role import Role, UserRole
from app.models.user import User
from app.schemas.auth import MeResponse, RefreshRequest, Token, UserCreate, UserResponse
from app.services.audit import log_action
from app.services.insurance import random_policy

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/sample-patient-data")
async def get_sample_patient_data():
    """Unauthenticated by design — this is a pre-registration convenience
    (the "Fill Sample Data" button on the Register screen), purely synthetic
    data with no real person behind it (see app/core/seed_data.py). Returns
    a full registration payload the frontend pre-fills and the user can
    still edit before submitting; nothing here is written to the database."""
    return random_patient_profile()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email already registered")

    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Username already taken")

    if user_data.register_as_patient:
        # Patient.email carries its own separate unique index from User.email
        # (not exempted by soft delete) — checked explicitly so a collision
        # surfaces as a clean 400 instead of an unhandled IntegrityError.
        result = await db.execute(select(Patient).where(Patient.email == user_data.email))
        if result.scalar_one_or_none():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A patient record already uses this email")

    user = User(
        email=user_data.email,
        username=user_data.username,
        hashed_password=get_password_hash(user_data.password),
    )
    db.add(user)
    await db.flush()

    if user_data.register_as_patient:
        insurance_provider = user_data.insurance_provider
        insurance_policy_number = user_data.insurance_policy_number
        # Same "blank insurance gets a random demo policy" fallback as
        # POST /patients/ (app/api/routes/patients.py::create_patient) — a
        # self-registered patient's eligibility checks shouldn't be any less
        # likely to work than an admin-created one just because they skipped
        # the field.
        if not insurance_provider and not insurance_policy_number:
            insurance_policy_number, insurance_provider = random_policy()

        patient = Patient(
            user_id=user.id,
            first_name=user_data.first_name,
            last_name=user_data.last_name,
            email=user_data.email,
            date_of_birth=user_data.date_of_birth,
            gender=user_data.gender,
            phone=user_data.phone,
            insurance_provider=insurance_provider,
            insurance_policy_number=insurance_policy_number,
            allergies=user_data.allergies,
        )
        db.add(patient)
        await db.flush()

        result = await db.execute(select(Role).where(Role.name == "patient"))
        role = result.scalar_one_or_none()
        if role is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "The 'patient' role is not seeded — run scripts/seed_roles.py",
            )
        # A direct join-row insert, not `user.roles.append(role)`: `user` is
        # freshly constructed in this request (never loaded with `roles`
        # eager-loaded, unlike admin.py's grant-role path), so touching the
        # relationship collection here would trigger a synchronous lazy-load
        # that isn't valid under an async session (MissingGreenlet).
        db.add(UserRole(user_id=user.id, role_id=role.id))
        await log_action(
            db, actor_id=user.id, action="patient.self_register",
            resource_type="patient", resource_id=patient.id,
        )

    await log_action(db, actor_id=user.id, action="auth.register", resource_type="user", resource_id=user.id)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_async_session),
):
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        await log_action(
            db, actor_id=user.id if user else None, action="auth.login_failed",
            resource_type="user", details={"username": form_data.username},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.username})
    refresh_token = await issue_refresh_token(db, user.id)
    await log_action(db, actor_id=user.id, action="auth.login", resource_type="user", resource_id=user.id)
    await db.commit()
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@router.post("/refresh", response_model=Token)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token)))
    stored = result.scalar_one_or_none()

    if stored is not None and stored.revoked_at is not None:
        # A refresh token that's already been rotated out is being reused —
        # either a replay of an old request or a stolen token. Worth auditing.
        await log_action(
            db, actor_id=stored.user_id, action="auth.refresh_token_reuse_detected",
            resource_type="refresh_token", resource_id=stored.id,
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    if stored is None or is_expired(stored.expires_at):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    new_raw = await rotate_refresh_token(db, stored)
    user = await db.get(User, stored.user_id)
    access_token = create_access_token(data={"sub": user.username})
    await log_action(db, actor_id=user.id, action="auth.refresh", resource_type="user", resource_id=user.id)
    await db.commit()
    return {"access_token": access_token, "refresh_token": new_raw, "token_type": "bearer"}


@router.get("/me", response_model=MeResponse)
async def me(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_async_session),
):
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == current_user.id)
    )
    user = result.scalar_one()
    roles = sorted(r.name for r in user.roles)
    permissions = sorted({p.name for role in user.roles for p in role.permissions})
    return MeResponse(id=user.id, username=user.username, email=user.email, roles=roles, permissions=permissions)


@router.post("/logout")
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token)))
    stored = result.scalar_one_or_none()

    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(timezone.utc)
        await log_action(
            db, actor_id=stored.user_id, action="auth.logout",
            resource_type="user", resource_id=stored.user_id,
        )
        await db.commit()

    return {"message": "Logged out"}
