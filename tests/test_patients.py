from httpx import AsyncClient


async def test_create_and_get_patient(test_client: AsyncClient, auth_headers, test_patient_data):
    response = await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    assert response.status_code == 201
    created = response.json()
    assert created["first_name"] == test_patient_data["first_name"]

    response = await test_client.get(f"/patients/{created['id']}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_get_nonexistent_patient(test_client: AsyncClient, auth_headers):
    response = await test_client.get("/patients/9999", headers=auth_headers)
    assert response.status_code == 404


async def test_update_patient_partial(test_client: AsyncClient, auth_headers, test_patient_data):
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


async def test_delete_patient(test_client: AsyncClient, auth_headers, test_patient_data):
    created = (
        await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)
    ).json()

    response = await test_client.delete(f"/patients/{created['id']}", headers=auth_headers)
    assert response.status_code == 200

    response = await test_client.get(f"/patients/{created['id']}", headers=auth_headers)
    assert response.status_code == 404


async def test_appointment_rejects_unknown_doctor(
    test_client: AsyncClient, auth_headers, test_patient_data
):
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
