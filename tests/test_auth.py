from datetime import date

from httpx import AsyncClient
from sqlalchemy import select

from app.core.seed import seed_roles_and_permissions
from app.models.patient import GenderEnum, Patient


async def test_register_user(test_client: AsyncClient, test_user_data):
    response = await test_client.post("/auth/register", json=test_user_data)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == test_user_data["email"]
    assert data["username"] == test_user_data["username"]
    assert "hashed_password" not in data
    assert "id" in data


async def test_register_duplicate_email(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    dupe = {**test_user_data, "username": "someone_else"}
    response = await test_client.post("/auth/register", json=dupe)
    assert response.status_code == 400


async def test_register_duplicate_username(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    dupe = {**test_user_data, "email": "other@example.com"}
    response = await test_client.post("/auth/register", json=dupe)
    assert response.status_code == 400


async def test_login_success(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    response = await test_client.post(
        "/auth/login",
        data={"username": test_user_data["username"], "password": test_user_data["password"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_login_invalid_credentials(test_client: AsyncClient):
    response = await test_client.post(
        "/auth/login", data={"username": "nonexistent", "password": "wrongpassword"}
    )
    assert response.status_code == 401


async def test_protected_route_requires_token(test_client: AsyncClient):
    response = await test_client.get("/patients/")
    assert response.status_code == 401


async def test_protected_route_rejects_bad_token(test_client: AsyncClient):
    response = await test_client.get(
        "/patients/", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


async def test_bare_registration_still_grants_no_role(test_client: AsyncClient, test_session, test_user_data):
    """Regression guard: register_as_patient defaults False, so a plain
    {email, username, password} registration must behave exactly as before —
    no role, no linked Patient row."""
    await test_client.post("/auth/register", json=test_user_data)
    login = await test_client.post(
        "/auth/login",
        data={"username": test_user_data["username"], "password": test_user_data["password"]},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = (await test_client.get("/auth/me", headers=headers)).json()
    assert me["roles"] == []

    patients = (await test_session.execute(select(Patient))).scalars().all()
    assert patients == []


async def test_register_as_patient_creates_linked_record_and_grants_role(
    test_client: AsyncClient, test_session, test_user_data
):
    await seed_roles_and_permissions(test_session)
    payload = {
        **test_user_data,
        "register_as_patient": True,
        "first_name": "Jamie",
        "last_name": "Rivera",
        "date_of_birth": "1990-01-01",
        "gender": "female",
    }
    response = await test_client.post("/auth/register", json=payload)
    assert response.status_code == 201

    login = await test_client.post(
        "/auth/login",
        data={"username": test_user_data["username"], "password": test_user_data["password"]},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = (await test_client.get("/auth/me", headers=headers)).json()
    assert me["roles"] == ["patient"]

    own_patients = (await test_client.get("/patients/", headers=headers)).json()["items"]
    assert len(own_patients) == 1
    assert own_patients[0]["first_name"] == "Jamie"
    assert own_patients[0]["email"] == test_user_data["email"]


async def test_register_as_patient_requires_demographics(test_client: AsyncClient, test_user_data):
    payload = {**test_user_data, "register_as_patient": True}
    response = await test_client.post("/auth/register", json=payload)
    assert response.status_code == 422


async def test_register_as_patient_rejects_duplicate_patient_email(
    test_client: AsyncClient, test_session, test_user_data, test_patient_data
):
    await seed_roles_and_permissions(test_session)
    # An existing Patient row (created some other way, e.g. by staff) already
    # uses this email — the pre-check must catch it before the insert, since
    # Patient.email's unique index isn't exempted by soft delete either.
    existing = Patient(
        first_name=test_patient_data["first_name"],
        last_name=test_patient_data["last_name"],
        email=test_user_data["email"],
        date_of_birth=date(1990, 1, 1),
        gender=GenderEnum.MALE,
    )
    test_session.add(existing)
    await test_session.commit()

    payload = {
        **test_user_data,
        "register_as_patient": True,
        "first_name": "Jamie",
        "last_name": "Rivera",
        "date_of_birth": "1990-01-01",
        "gender": "female",
    }
    response = await test_client.post("/auth/register", json=payload)
    assert response.status_code == 400
