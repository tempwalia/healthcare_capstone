from httpx import AsyncClient

from tests.test_referral import _grant_role


async def test_create_and_get_patient(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")

    response = await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    assert response.status_code == 201
    created = response.json()
    assert created["first_name"] == test_patient_data["first_name"]

    response = await test_client.get(f"/patients/{created['id']}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_get_nonexistent_patient(
    test_client: AsyncClient, test_session, test_user_data, auth_headers
):
    """A `patient`-role user with no linked patient record sees nothing
    (falls through the visibility filter to a real 404), not a 403 leaking
    the existence check — same not-found-vs-forbidden shape as `referral.py`."""
    await _grant_role(test_session, test_user_data["username"], "patient")

    response = await test_client.get("/patients/9999", headers=auth_headers)
    assert response.status_code == 404


async def test_update_patient_partial(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")

    created = (
        await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    ).json()

    response = await test_client.put(
        f"/patients/{created['id']}", json={"phone": "+19998887777"}, headers=auth_headers
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["phone"] == "+19998887777"
    assert updated["first_name"] == test_patient_data["first_name"]


async def test_delete_patient(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")

    created = (
        await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    ).json()

    response = await test_client.delete(f"/patients/{created['id']}", headers=auth_headers)
    assert response.status_code == 200

    response = await test_client.get(f"/patients/{created['id']}", headers=auth_headers)
    assert response.status_code == 404


async def test_appointment_rejects_unknown_doctor(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")

    patient = (
        await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    ).json()

    response = await test_client.post(
        "/appointments/",
        json={
            "patient_id": patient["id"],
            "doctor_id": 9999,
            "appointment_datetime": "2026-09-01T10:00:00Z",
        },
        headers=auth_headers,
    )
    assert response.status_code == 404
