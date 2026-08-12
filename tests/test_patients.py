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


async def test_patients_search_matches_name_email_or_phone(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    """GET /patients/?q= — a real server-side search, replacing the
    dashboard's old current-page-only client-side filter (static/js/resource.js).
    Still respects the caller's own patient_visibility_filter (not tested
    here directly — that scope is exercised elsewhere; this just confirms
    the `q` match itself)."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    other = {**test_patient_data, "first_name": "Zora", "last_name": "Whitmore",
             "email": "zora.whitmore@example.com", "phone": "555-999-8888"}
    await test_client.post("/patients/", json=other, headers=auth_headers)

    by_name = await test_client.get("/patients/?q=Zora", headers=auth_headers)
    assert by_name.status_code == 200
    names = {p["first_name"] for p in by_name.json()["items"]}
    assert names == {"Zora"}

    by_phone = await test_client.get("/patients/?q=999-8888", headers=auth_headers)
    assert {p["last_name"] for p in by_phone.json()["items"]} == {"Whitmore"}

    no_match = await test_client.get("/patients/?q=NoSuchPersonAtAll", headers=auth_headers)
    assert no_match.json()["items"] == []


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
