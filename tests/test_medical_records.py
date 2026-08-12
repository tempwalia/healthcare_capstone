"""Medical record create/update/delete ownership scoping and the
patient/care_coordinator medical_record:manage grant — "at every stage
either user or care coordinator or doctor should have the option to add and
delete the patient medical records" (explicit user request). Companion to
`tests/test_record_scope.py`'s GET-side scoping coverage; this file covers
the mutation routes, which previously had zero ownership scoping at all —
see app/api/routes/medical_records.py."""
from httpx import AsyncClient

from tests.test_record_scope import _reset_roles
from tests.test_referral import _grant_role, _link_doctor_to_user, _link_patient_to_user


async def test_patient_can_create_update_delete_their_own_medical_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient["id"], test_user_data["username"])

    created = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z",
              "record_type": "lab_result", "diagnosis": "Self-reported cholesterol panel"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    record = created.json()

    updated = await test_client.put(
        f"/medical-records/{record['id']}", json={"notes": "Updated by patient"}, headers=auth_headers
    )
    assert updated.status_code == 200
    assert updated.json()["notes"] == "Updated by patient"

    deleted = await test_client.delete(f"/medical-records/{record['id']}", headers=auth_headers)
    assert deleted.status_code == 200


async def test_patient_cannot_create_or_write_another_patients_medical_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient_a = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    other_data = {**test_patient_data, "email": "other.patient@example.com"}
    patient_b = (await test_client.post("/patients/", json=other_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_record = (await test_client.post(
        "/medical-records/",
        json={"patient_id": patient_b["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient_a["id"], test_user_data["username"])

    blocked_create = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient_b["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-02T09:00:00Z"},
        headers=auth_headers,
    )
    assert blocked_create.status_code == 403

    blocked_update = await test_client.put(
        f"/medical-records/{other_record['id']}", json={"notes": "should not work"}, headers=auth_headers
    )
    assert blocked_update.status_code == 404  # scoped lookup — not a leak, looks like it doesn't exist

    blocked_delete = await test_client.delete(f"/medical-records/{other_record['id']}", headers=auth_headers)
    assert blocked_delete.status_code == 404


async def test_specialist_cannot_author_a_record_under_another_doctors_name(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """medical_record:manage no longer means "attribute this record to any
    doctor" — a view_own-scoped caller (pcp/specialist) must write under
    their own linked doctor_id."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor_a = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_doctor_data = {**test_doctor_data, "email": "other.specialist@example.com", "license_number": "MD-OTHER-1"}
    doctor_b = (await test_client.post("/doctors/", json=other_doctor_data, headers=auth_headers)).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "specialist")
    await _link_doctor_to_user(test_session, doctor_a["id"], test_user_data["username"])

    blocked = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor_b["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )
    assert blocked.status_code == 403

    allowed = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor_a["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )
    assert allowed.status_code == 201


async def test_care_coordinator_can_add_and_delete_medical_records(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """care_coordinator gained medical_record:manage (was view-only) per
    explicit request — coordinators can now add/delete records for any
    patient, same broad-access shape as their existing patient:manage."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    created = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z",
              "record_type": "prescription"},
        headers=auth_headers,
    )
    assert created.status_code == 201

    deleted = await test_client.delete(f"/medical-records/{created.json()['id']}", headers=auth_headers)
    assert deleted.status_code == 200
