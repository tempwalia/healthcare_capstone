"""GET /doctors/me and GET /appointments/?doctor_id=&upcoming_only= — the
"which Doctor row is mine" lookup and the scoped query a provider's My Day
view is built on. See app/api/routes/doctors.py::get_my_doctor_record and
app/api/routes/appointments.py::get_appointments."""
from httpx import AsyncClient

from tests.test_record_scope import _reset_roles
from tests.test_referral import _grant_role, _link_doctor_to_user, _link_patient_to_user, _register_and_login


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


async def test_appointment_attached_record_404_when_none_attached(
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

    response = await test_client.get(f"/appointments/{appointment['id']}/attached-record", headers=auth_headers)
    assert response.status_code == 404


async def test_appointment_attached_record_visible_to_assigned_doctor_not_owning_the_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """Same gap as the referral-side test — a patient's doctor-less quick
    upload (doctor_id=None) is only visible to the assigned doctor because
    it's attached to an appointment they're party to, not because they own
    the record."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    assigned_doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    unrelated_data = {**test_doctor_data, "email": "appt.unrelated@example.com", "license_number": "MD-APPT-UNREL-1"}
    unrelated_doctor = (await test_client.post("/doctors/", json=unrelated_data, headers=auth_headers)).json()
    await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": assigned_doctor["id"], "weekday": 0, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30},
        headers=auth_headers,
    )
    slots = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": assigned_doctor["id"], "days_ahead": 14}, headers=auth_headers
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient["id"], test_user_data["username"])
    uploaded = (await test_client.post(
        "/medical-records/quick-upload",
        headers=auth_headers,
        data={"patient_id": str(patient["id"]), "record_type": "lab_result"},
        files={"file": ("appt_bloodwork.pdf", b"appointment lab results", "application/pdf")},
    )).json()
    booked = (await test_client.post(
        f"/schedule/slots/{slots[0]['id']}/book",
        json={"patient_id": patient["id"], "medical_record_id": uploaded["medical_record_id"]},
        headers=auth_headers,
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "specialist")
    await _link_doctor_to_user(test_session, assigned_doctor["id"], test_user_data["username"])

    attached = await test_client.get(f"/appointments/{booked['id']}/attached-record", headers=auth_headers)
    assert attached.status_code == 200
    body = attached.json()
    assert body["record"]["id"] == uploaded["medical_record_id"]
    document_id = body["documents"][0]["id"]

    downloaded = await test_client.get(f"/medical-records/documents/{document_id}/download", headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"appointment lab results"

    # A separate account — Doctor.user_id is unique, so the shared test user
    # (already linked to `assigned_doctor` above) can't also link to a
    # second doctor.
    unrelated_headers = await _register_and_login(test_client, "appt_unrelated_specialist")
    await _grant_role(test_session, "appt_unrelated_specialist", "specialist")
    await _link_doctor_to_user(test_session, unrelated_doctor["id"], "appt_unrelated_specialist")

    blocked = await test_client.get(f"/appointments/{booked['id']}/attached-record", headers=unrelated_headers)
    assert blocked.status_code == 404
