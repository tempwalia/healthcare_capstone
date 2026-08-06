"""Functional test evidence for each mocked external system's integration
contract (Phase 3) — hits each mock app directly, independent of the main
platform's DB/auth, since these represent standalone external systems."""
from httpx import ASGITransport, AsyncClient

from mock_systems.ehr_mock.main import app as ehr_app
from mock_systems.notification_mock.main import app as notification_app
from mock_systems.payer_mock.main import app as payer_app
from mock_systems.provider_directory_mock.main import app as directory_app
from mock_systems.scheduling_mock.main import app as scheduling_app


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://mock")


async def test_ehr_returns_seeded_history_for_known_patient():
    async with await _client(ehr_app) as client:
        response = await client.get("/patients/1/history")
    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == 1
    assert "M54.5 - Low back pain" in body["prior_diagnoses"]


async def test_ehr_returns_empty_history_for_unknown_patient():
    async with await _client(ehr_app) as client:
        response = await client.get("/patients/9999/history")
    assert response.status_code == 200
    assert response.json()["prior_diagnoses"] == []


async def test_payer_verifies_known_policy_and_flags_prior_auth():
    async with await _client(payer_app) as client:
        response = await client.post(
            "/eligibility/check",
            json={"insurance_policy_number": "ACME-991123", "procedure_code": "M51.26"},
        )
    body = response.json()
    assert body["verified"] is True
    assert body["network_status"] == "in_network"
    assert body["prior_auth_required"] is True


async def test_payer_rejects_unknown_policy():
    async with await _client(payer_app) as client:
        response = await client.post(
            "/eligibility/check",
            json={"insurance_policy_number": "NOT-A-REAL-PLAN", "procedure_code": "M51.26"},
        )
    body = response.json()
    assert body["verified"] is False
    assert body["network_status"] == "unknown"


async def test_directory_filters_by_specialty_and_flags_network_status():
    async with await _client(directory_app) as client:
        response = await client.get(
            "/providers/search", params={"specialty": "Orthopedics", "insurance_plan_id": 1}
        )
    body = response.json()
    assert len(body) >= 2
    assert all(p["specialty"] == "Orthopedics" for p in body)
    by_id = {p["doctor_id"]: p for p in body}
    assert by_id[88]["in_network"] is True  # doctor 88 accepts plan 1
    assert by_id[73]["in_network"] is False  # doctor 73 accepts no plans


async def test_scheduling_availability_then_book_round_trip():
    async with await _client(scheduling_app) as client:
        availability = await client.get("/availability", params={"doctor_id": 88, "within_days": 7})
        slots = availability.json()
        assert len(slots) > 0

        first_slot = slots[0]
        booked = await client.post(
            "/slots/book", json={"doctor_id": 88, "slot_id": first_slot["slot_id"]}
        )
        assert booked.status_code == 200
        assert booked.json()["status"] == "confirmed"

        # booking the same slot again must fail — it's no longer available
        conflict = await client.post(
            "/slots/book", json={"doctor_id": 88, "slot_id": first_slot["slot_id"]}
        )
        assert conflict.status_code == 409

        # and it should no longer show up in a fresh availability listing
        refreshed = await client.get("/availability", params={"doctor_id": 88, "within_days": 7})
        assert first_slot["slot_id"] not in {s["slot_id"] for s in refreshed.json()}


async def test_notification_send_records_delivery():
    async with await _client(notification_app) as client:
        response = await client.post(
            "/notifications/send",
            json={"user_id": 1, "channel": "email", "message": "Your referral is confirmed."},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["delivery_status"] == "delivered"
    assert body["channel"] == "email"
