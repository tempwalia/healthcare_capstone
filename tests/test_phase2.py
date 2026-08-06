from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.role import Permission, Role
from app.models.user import User
from tests.test_referral import _grant_role


async def _grant_admin(test_session, username: str) -> None:
    user = (
        await test_session.execute(
            select(User).options(selectinload(User.roles)).where(User.username == username)
        )
    ).scalar_one()
    admin_role = Role(name="admin", description="Platform administrator")
    permission = Permission(name="admin:*", description="bypass")
    admin_role.permissions.append(permission)
    user.roles.append(admin_role)
    test_session.add_all([admin_role, permission])
    await test_session.commit()


async def test_login_issues_refresh_token(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    response = await test_client.post(
        "/auth/login",
        data={"username": test_user_data["username"], "password": test_user_data["password"]},
    )
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body


async def test_refresh_token_rotates_and_old_token_is_rejected(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    login = (await test_client.post(
        "/auth/login",
        data={"username": test_user_data["username"], "password": test_user_data["password"]},
    )).json()

    refreshed = await test_client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refreshed.status_code == 200
    # the refresh token must rotate (random per issuance); the access token is
    # a deterministic JWT and can legitimately collide if issued within the
    # same second for the same user, so it's not a meaningful thing to assert.
    assert refreshed.json()["refresh_token"] != login["refresh_token"]

    reused = await test_client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert reused.status_code == 401


async def test_logout_revokes_refresh_token(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    login = (await test_client.post(
        "/auth/login",
        data={"username": test_user_data["username"], "password": test_user_data["password"]},
    )).json()

    await test_client.post("/auth/logout", json={"refresh_token": login["refresh_token"]})
    response = await test_client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert response.status_code == 401


async def test_login_is_rate_limited(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    bad_login = {"username": test_user_data["username"], "password": "wrong-password"}

    responses = [await test_client.post("/auth/login", data=bad_login) for _ in range(6)]
    assert responses[-1].status_code == 429
    assert all(r.status_code == 401 for r in responses[:5])


async def test_audit_log_requires_permission(test_client: AsyncClient, auth_headers):
    response = await test_client.get("/audit/", headers=auth_headers)
    assert response.status_code == 403


async def test_audit_log_visible_to_admin_and_records_actions(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_admin(test_session, test_user_data["username"])
    await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)

    response = await test_client.get("/audit/", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    actions = {row["action"] for row in body["items"]}
    assert "patient.create" in actions


async def test_list_endpoints_return_pagination_envelope(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")

    await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    response = await test_client.get("/patients/", headers=auth_headers)
    body = response.json()
    assert set(body.keys()) == {"items", "total", "skip", "limit", "next"}
    assert body["total"] >= 1


async def test_soft_deleted_patient_excluded_from_list(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")

    created = (
        await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    ).json()
    await test_client.delete(f"/patients/{created['id']}", headers=auth_headers)

    listed = (await test_client.get("/patients/", headers=auth_headers)).json()
    assert all(p["id"] != created["id"] for p in listed["items"])


async def test_health_ready_reports_database_status(test_client: AsyncClient):
    response = await test_client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["database"] is True
