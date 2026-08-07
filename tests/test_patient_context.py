"""Workstream A — GET /patients/{id}/context aggregates appointments/medical
records/referrals/care-team for a patient using the exact same visibility
filters their own standalone list endpoints use, so this aggregation is never
broader than what the caller could already assemble by hand."""
from httpx import AsyncClient

from tests.test_referral import _grant_role, _link_doctor_to_user, _link_patient_to_user
from tests.test_record_scope import _reset_roles


async def test_context_aggregates_everything_visible_to_a_coordinator(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    # pcp holds the :manage/:create permissions needed to seed the fixtures;
    # care_coordinator (granted after, for the actual assertion below) holds
    # the *_view_all breadth instead, per the deliberate split in
    # app/core/seed.py (care_coordinator never gets medical_record:manage —
    # coordinators don't author clinical notes).
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post(
        "/patients/",
        json={**test_patient_data, "insurance_provider": "Acme Health", "insurance_policy_number": "ACME-991123"},
        headers=auth_headers,
    )).json()
    referring_doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    specialist_doctor = (await test_client.post(
        "/doctors/", json={**test_doctor_data, "email": "specialist@example.com", "license_number": "MD777777"},
        headers=auth_headers,
    )).json()

    appointment = (await test_client.post(
        "/appointments/",
        json={
            "patient_id": patient["id"], "doctor_id": referring_doctor["id"],
            "appointment_datetime": "2026-09-01T10:00:00Z",
        },
        headers=auth_headers,
    )).json()
    record = (await test_client.post(
        "/medical-records/",
        json={
            "patient_id": patient["id"], "doctor_id": referring_doctor["id"],
            "visit_date": "2026-07-01T09:00:00Z", "diagnosis": "Routine checkup",
        },
        headers=auth_headers,
    )).json()
    referral = (await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"], "referring_doctor_id": referring_doctor["id"],
            "specialist_id": specialist_doctor["id"], "request_date": "2026-08-01", "reason": "Back pain",
        },
        headers=auth_headers,
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")

    response = await test_client.get(f"/patients/{patient['id']}/context", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["patient_id"] == patient["id"]
    assert body["insurance"] == {"provider": "Acme Health", "policy_number": "ACME-991123"}
    assert {a["id"] for a in body["appointments"]} == {appointment["id"]}
    assert {r["id"] for r in body["medical_records"]} == {record["id"]}
    assert {r["id"] for r in body["referrals"]} == {referral["id"]}
    care_team_ids = {c["doctor_id"] for c in body["care_team"]}
    assert care_team_ids == {referring_doctor["id"], specialist_doctor["id"]}


async def test_context_hides_records_the_caller_cannot_see_even_for_a_visible_patient(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """A pcp holds patient:view_all (broad, deliberate — see record_scope.py),
    so the patient itself is visible, but appointment/medical-record
    visibility stays need-to-know — an appointment with a *different* doctor
    must not leak into this pcp's view of the patient's context."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor_a = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    doctor_b = (await test_client.post(
        "/doctors/", json={**test_doctor_data, "email": "doctor.b@example.com", "license_number": "MD555555"},
        headers=auth_headers,
    )).json()

    await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor_b["id"], "appointment_datetime": "2026-09-01T10:00:00Z"},
        headers=auth_headers,
    )
    own_appointment = (await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor_a["id"], "appointment_datetime": "2026-09-02T10:00:00Z"},
        headers=auth_headers,
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "pcp")
    await _link_doctor_to_user(test_session, doctor_a["id"], test_user_data["username"])

    response = await test_client.get(f"/patients/{patient['id']}/context", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert {a["id"] for a in body["appointments"]} == {own_appointment["id"]}
    assert body["care_team"] == [{"doctor_id": doctor_a["id"], "role": "treating"}]


async def test_patient_role_sees_own_context_but_not_anothers(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient_a = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    patient_b = (await test_client.post(
        "/patients/", json={**test_patient_data, "email": "other@example.com"}, headers=auth_headers
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient_a["id"], test_user_data["username"])

    own = await test_client.get(f"/patients/{patient_a['id']}/context", headers=auth_headers)
    assert own.status_code == 200

    other = await test_client.get(f"/patients/{patient_b['id']}/context", headers=auth_headers)
    assert other.status_code == 404
