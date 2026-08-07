from httpx import AsyncClient

from tests.test_record_scope import _reset_roles
from tests.test_referral import _grant_role, _link_patient_to_user


async def _create_doctor(test_client, headers, test_doctor_data):
    return (await test_client.post("/doctors/", json=test_doctor_data, headers=headers)).json()


async def test_generate_slots_from_availability(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    doctor = await _create_doctor(test_client, auth_headers, test_doctor_data)

    availability = await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": doctor["id"], "weekday": 0, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30},
        headers=auth_headers,
    )
    assert availability.status_code == 201

    generated = await test_client.post(
        "/schedule/slots/generate",
        json={"doctor_id": doctor["id"], "days_ahead": 14},
        headers=auth_headers,
    )
    assert generated.status_code == 200
    slots = generated.json()
    assert len(slots) > 0
    assert all(s["doctor_id"] == doctor["id"] and not s["is_booked"] for s in slots)


async def test_generate_slots_is_idempotent(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    doctor = await _create_doctor(test_client, auth_headers, test_doctor_data)
    await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": doctor["id"], "weekday": 0, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30},
        headers=auth_headers,
    )

    first = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": doctor["id"], "days_ahead": 14}, headers=auth_headers
    )).json()
    second = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": doctor["id"], "days_ahead": 14}, headers=auth_headers
    )).json()

    assert len(first) > 0
    assert len(second) == 0  # nothing new to generate the second time


async def test_book_slot_creates_appointment_and_marks_slot_booked(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data, test_patient_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    doctor = await _create_doctor(test_client, auth_headers, test_doctor_data)
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": doctor["id"], "weekday": 0, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30},
        headers=auth_headers,
    )
    slots = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": doctor["id"], "days_ahead": 14}, headers=auth_headers
    )).json()
    slot_id = slots[0]["id"]

    booked = await test_client.post(
        f"/schedule/slots/{slot_id}/book",
        json={"patient_id": patient["id"], "reason": "Follow-up"},
        headers=auth_headers,
    )
    assert booked.status_code == 201
    appointment = booked.json()
    assert appointment["patient_id"] == patient["id"]
    assert appointment["doctor_id"] == doctor["id"]

    conflict = await test_client.post(
        f"/schedule/slots/{slot_id}/book",
        json={"patient_id": patient["id"]},
        headers=auth_headers,
    )
    assert conflict.status_code == 409

    remaining = (await test_client.get(
        "/schedule/slots/", params={"doctor_id": doctor["id"], "is_booked": False}, headers=auth_headers
    )).json()
    assert all(s["id"] != slot_id for s in remaining["items"])


async def test_bare_patient_role_cannot_create_availability_or_generate_slots_but_can_book(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data, test_patient_data
):
    """Workstream F-1: /schedule/availability/ and /schedule/slots/generate
    previously had no permission check at all — any authenticated user,
    including a bare `patient`, could call them. Now gated behind
    appointment:manage (staff-only). /schedule/slots/{id}/book stays open —
    that's the patient self-service "Book an Appointment" flow."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    doctor = await _create_doctor(test_client, auth_headers, test_doctor_data)
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": doctor["id"], "weekday": 0, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30},
        headers=auth_headers,
    )
    slots = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": doctor["id"], "days_ahead": 14}, headers=auth_headers
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient["id"], test_user_data["username"])

    denied_availability = await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": doctor["id"], "weekday": 1, "start_time": "09:00", "end_time": "10:00"},
        headers=auth_headers,
    )
    assert denied_availability.status_code == 403

    denied_generate = await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": doctor["id"], "days_ahead": 14}, headers=auth_headers,
    )
    assert denied_generate.status_code == 403

    allowed_book = await test_client.post(
        f"/schedule/slots/{slots[0]['id']}/book",
        json={"patient_id": patient["id"], "reason": "Follow-up"},
        headers=auth_headers,
    )
    assert allowed_book.status_code == 201


async def test_generate_slots_for_doctor_without_availability_returns_empty(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    doctor = await _create_doctor(test_client, auth_headers, test_doctor_data)
    response = await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": doctor["id"], "days_ahead": 14}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == []
