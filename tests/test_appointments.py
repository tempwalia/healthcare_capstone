"""GET /doctors/me and GET /appointments/?doctor_id=&upcoming_only= — the
"which Doctor row is mine" lookup and the scoped query a provider's My Day
view is built on. See app/api/routes/doctors.py::get_my_doctor_record and
app/api/routes/appointments.py::get_appointments."""
from httpx import AsyncClient

from tests.test_record_scope import _reset_roles
from tests.test_referral import _grant_role, _link_doctor_to_user, _link_patient_to_user


async def test_doctors_me_404s_for_an_unlinked_account(test_client: AsyncClient, test_session, test_user_data, auth_headers):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    response = await test_client.get("/doctors/me", headers=auth_headers)
    assert response.status_code == 404


async def test_doctors_me_returns_the_linked_doctor_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await _link_doctor_to_user(test_session, doctor["id"], test_user_data["username"])

    response = await test_client.get("/doctors/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == doctor["id"]


async def test_appointments_doctor_id_and_upcoming_only_narrow_the_query(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor_a = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_doctor_data = {**test_doctor_data, "email": "other.doc@example.com", "license_number": "MD555555"}
    doctor_b = (await test_client.post("/doctors/", json=other_doctor_data, headers=auth_headers)).json()

    past = (await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor_a["id"], "appointment_datetime": "2020-01-01T10:00:00Z"},
        headers=auth_headers,
    )).json()
    upcoming = (await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor_a["id"], "appointment_datetime": "2099-01-01T10:00:00Z"},
        headers=auth_headers,
    )).json()
    other_doctor_upcoming = (await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor_b["id"], "appointment_datetime": "2099-01-02T10:00:00Z"},
        headers=auth_headers,
    )).json()

    by_doctor = (await test_client.get(
        f"/appointments/?doctor_id={doctor_a['id']}", headers=auth_headers
    )).json()
    assert {a["id"] for a in by_doctor["items"]} == {past["id"], upcoming["id"]}

    upcoming_for_doctor = (await test_client.get(
        f"/appointments/?doctor_id={doctor_a['id']}&upcoming_only=true", headers=auth_headers
    )).json()
    assert {a["id"] for a in upcoming_for_doctor["items"]} == {upcoming["id"]}

    assert other_doctor_upcoming["id"] not in {a["id"] for a in upcoming_for_doctor["items"]}


async def test_direct_booked_appointment_outcome_completes_and_generates_summary(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """The gap this closes: a doctor "has no process to take on" a patient
    who books an appointment directly (no referral involved at all) —
    POST /appointments/{id}/outcome reuses the exact same outcome+summary
    pipeline as a referral consult (app/services/referral_outcome.py),
    just keyed on appointment_id instead of referral_request_id."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    appointment = (await test_client.post(
        "/appointments/",
        json={"patient_id": patient["id"], "doctor_id": doctor["id"], "appointment_datetime": "2026-09-01T10:00:00Z",
              "reason": "Annual physical"},
        headers=auth_headers,
    )).json()
    assert appointment["referral_id"] is None

    outcome_response = await test_client.post(
        f"/appointments/{appointment['id']}/outcome", headers=auth_headers,
        json={"symptoms": "Fatigue", "diagnosis": "Mild anemia", "prescription": "Iron supplement",
              "follow_up_notes": "Recheck bloodwork in 6 weeks"},
    )
    assert outcome_response.status_code == 202
    outcome = outcome_response.json()
    assert outcome["appointment_id"] == appointment["id"]
    assert outcome["referral_request_id"] is None

    final = (await test_client.get(f"/appointments/{appointment['id']}", headers=auth_headers)).json()
    assert final["status"] == "completed"

    # Duplicate outcome for the same appointment is rejected
    duplicate = await test_client.post(
        f"/appointments/{appointment['id']}/outcome", headers=auth_headers, json={"symptoms": "should not work"}
    )
    assert duplicate.status_code == 409

    fetched_outcome = (await test_client.get(f"/appointments/{appointment['id']}/outcome", headers=auth_headers)).json()
    assert fetched_outcome["diagnosis"] == "Mild anemia"
    assert fetched_outcome["interaction_summary"]  # background task runs synchronously under ASGITransport in tests

    # The completion summary should also have landed as a real MedicalRecord
    # on the patient's chart, same as the referral-outcome path does.
    context = (await test_client.get(f"/patients/{patient['id']}/context", headers=auth_headers)).json()
    appointment_records = [r for r in context["medical_records"] if r.get("record_type") == "appointment_consult"]
    assert len(appointment_records) == 1
    assert appointment_records[0]["diagnosis"] == "Mild anemia"


async def test_specialist_can_only_complete_their_own_appointment(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor_a = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_doctor_data = {**test_doctor_data, "email": "other.outcome.doc@example.com", "license_number": "MD-OUT-1"}
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
    await _grant_role(test_session, test_user_data["username"], "specialist")
    await _link_doctor_to_user(test_session, doctor_a["id"], test_user_data["username"])

    blocked = await test_client.post(
        f"/appointments/{appt_b['id']}/outcome", headers=auth_headers, json={"symptoms": "should not be allowed"}
    )
    assert blocked.status_code == 404  # scoped lookup, not a leak

    allowed = await test_client.post(
        f"/appointments/{appt_a['id']}/outcome", headers=auth_headers, json={"symptoms": "Headache"}
    )
    assert allowed.status_code == 202


async def test_patient_can_view_but_not_record_their_appointment_outcome(
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
    await test_client.post(
        f"/appointments/{appointment['id']}/outcome", headers=auth_headers,
        json={"diagnosis": "Seasonal allergies"},
    )

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient["id"], test_user_data["username"])

    cannot_record = await test_client.post(
        f"/appointments/{appointment['id']}/outcome", headers=auth_headers, json={"diagnosis": "attempted"}
    )
    assert cannot_record.status_code == 403  # patient role lacks referral:record_outcome

    can_view = await test_client.get(f"/appointments/{appointment['id']}/outcome", headers=auth_headers)
    assert can_view.status_code == 200
    assert can_view.json()["diagnosis"] == "Seasonal allergies"
