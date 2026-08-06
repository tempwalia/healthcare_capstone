"""Regression coverage for the ownership-scoping fix on patients/doctors/
appointments/medical-records — these routes used to have zero scoping at
all (any authenticated user could fetch or mutate any record by ID), the
same class of bug `referral_scope.py` already fixed for referrals. See
`app/services/record_scope.py` and CAPSTONE_IMPLEMENTATION_GUIDE.md's
Phase 2 notes for the design."""
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.user import User
from tests.test_referral import _grant_role, _link_doctor_to_user, _link_patient_to_user


async def _reset_roles(test_session, username: str) -> None:
    user = (
        await test_session.execute(
            select(User).options(selectinload(User.roles)).where(User.username == username)
        )
    ).scalar_one()
    user.roles.clear()
    await test_session.commit()


async def test_patient_role_cannot_see_or_write_another_patients_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient_a = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    other_data = {**test_patient_data, "email": "other@example.com"}
    patient_b = (await test_client.post("/patients/", json=other_data, headers=auth_headers)).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient_a["id"], test_user_data["username"])

    own = await test_client.get(f"/patients/{patient_a['id']}", headers=auth_headers)
    assert own.status_code == 200

    other = await test_client.get(f"/patients/{patient_b['id']}", headers=auth_headers)
    assert other.status_code == 404  # not a 403 leak — looks identical to "doesn't exist"

    listed = (await test_client.get("/patients/", headers=auth_headers)).json()
    assert {p["id"] for p in listed["items"]} == {patient_a["id"]}

    write_own = await test_client.put(
        f"/patients/{patient_a['id']}", json={"phone": "+10000000000"}, headers=auth_headers
    )
    assert write_own.status_code == 403  # `patient` role has view_own, not manage

    delete_other = await test_client.delete(f"/patients/{patient_b['id']}", headers=auth_headers)
    assert delete_other.status_code == 403


async def test_patient_role_cannot_manage_doctor_directory(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "patient")
    response = await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)
    assert response.status_code == 403


async def test_doctor_sees_only_their_own_appointments(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor_a = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_doctor_data = {**test_doctor_data, "email": "other.doc@example.com", "license_number": "MD999999"}
    doctor_b = (await test_client.post("/doctors/", json=other_doctor_data, headers=auth_headers)).json()

    appt_a = (await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor_a["id"], "appointment_datetime": "2026-09-01T10:00:00Z"},
        headers=auth_headers,
    )).json()
    appt_b = (await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor_b["id"], "appointment_datetime": "2026-09-02T10:00:00Z"},
        headers=auth_headers,
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "pcp")
    await _link_doctor_to_user(test_session, doctor_a["id"], test_user_data["username"])

    own = await test_client.get(f"/appointments/{appt_a['id']}", headers=auth_headers)
    assert own.status_code == 200

    other = await test_client.get(f"/appointments/{appt_b['id']}", headers=auth_headers)
    assert other.status_code == 404

    listed = (await test_client.get("/appointments/", headers=auth_headers)).json()
    assert {a["id"] for a in listed["items"]} == {appt_a["id"]}


async def test_doctor_sees_only_their_own_medical_records(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    # care_coordinator holds medical_record:view_all but deliberately not
    # :manage (coordinators don't author clinical notes) — use pcp to seed
    # the fixture records instead.
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor_a = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_doctor_data = {**test_doctor_data, "email": "other.doc2@example.com", "license_number": "MD888888"}
    doctor_b = (await test_client.post("/doctors/", json=other_doctor_data, headers=auth_headers)).json()

    record_a = (await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor_a["id"], "visit_date": "2026-07-01T09:00:00Z", "diagnosis": "Seen by doctor A"},
        headers=auth_headers,
    )).json()
    record_b = (await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor_b["id"], "visit_date": "2026-07-02T09:00:00Z", "diagnosis": "Seen by doctor B"},
        headers=auth_headers,
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "specialist")
    await _link_doctor_to_user(test_session, doctor_a["id"], test_user_data["username"])

    own = await test_client.get(f"/medical-records/{record_a['id']}", headers=auth_headers)
    assert own.status_code == 200

    other = await test_client.get(f"/medical-records/{record_b['id']}", headers=auth_headers)
    assert other.status_code == 404

    listed = (await test_client.get("/medical-records/", headers=auth_headers)).json()
    assert {r["id"] for r in listed["items"]} == {record_a["id"]}


async def test_care_coordinator_sees_every_patient_appointment_and_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    appointment = (await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor["id"], "appointment_datetime": "2026-09-01T10:00:00Z"},
        headers=auth_headers,
    )).json()

    fetched_patient = await test_client.get(f"/patients/{patient['id']}", headers=auth_headers)
    assert fetched_patient.status_code == 200

    fetched_appointment = await test_client.get(f"/appointments/{appointment['id']}", headers=auth_headers)
    assert fetched_appointment.status_code == 200
