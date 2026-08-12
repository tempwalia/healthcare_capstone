"""Phases 6-7 — the real LangGraph referral workflow (orchestration,
document processing/extraction), exercised end-to-end against the fake MCP
tool layer in tests/agent_fakes.py (see conftest's `agent_graph_test_setup`).
"""
from httpx import AsyncClient

from mock_systems.notification_mock.main import (
    sent_messages as notification_sent_messages,
)
from tests.test_referral import _grant_role, _link_doctor_to_user, _link_patient_to_user


async def _upload_required_documents(
    test_client: AsyncClient, auth_headers, referral_id: int,
    *, letter_text: str = "Routine referral letter.",
    imaging_text: str = "MRI imaging report: unremarkable.",
):
    """Uploads one document matching each of `intake_node`'s
    REQUIRED_DOC_TYPES keyword heuristics (filename-based). Each upload
    re-triggers the background workflow while the referral is still
    `awaiting_documents` — by the time the second call returns, the workflow
    has re-run `intake_node` and (if both types are now present) moved on."""
    await test_client.post(
        f"/referral/requests/{referral_id}/documents", headers=auth_headers,
        files={"file": ("referral_letter.txt", letter_text.encode(), "text/plain")},
    )
    return await test_client.post(
        f"/referral/requests/{referral_id}/documents", headers=auth_headers,
        files={"file": ("recent_imaging_report.txt", imaging_text.encode(), "text/plain")},
    )


async def _submit_referral(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
    *, insurance_policy_number: str, reason: str,
    letter_text: str = "Routine referral letter.",
    imaging_text: str = "MRI imaging report: unremarkable.",
    specialist_id=None,
    preferred_slot_id=None,
):
    await _grant_role(test_session, test_user_data["username"], "pcp")

    patient_data = {
        "first_name": "Jamie",
        "last_name": "Rivera",
        "email": "jamie.rivera@example.com",
        "phone": "+15550001111",
        "date_of_birth": "1985-04-12",
        "gender": "female",
        "insurance_provider": "Acme Health",
        "insurance_policy_number": insurance_policy_number,
    }
    patient = (await test_client.post("/patients/", json=patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await _link_doctor_to_user(test_session, doctor["id"], test_user_data["username"])

    # A separate account linked to the patient, so notify_node has a
    # `user_id` to message once the appointment is booked.
    await test_client.post(
        "/auth/register",
        json={"email": "jamie.login@example.com", "username": "jamie_login", "password": "testpassword123"},
    )
    await _link_patient_to_user(test_session, patient["id"], "jamie_login")

    payload = {
        "patient_id": patient["id"],
        "referring_doctor_id": doctor["id"],
        "request_date": "2026-08-06",
        "reason": reason,
    }
    if specialist_id is not None:
        payload["specialist_id"] = specialist_id
    if preferred_slot_id is not None:
        payload["preferred_slot_id"] = preferred_slot_id
    response = await test_client.post("/referral/requests/", json=payload, headers=auth_headers)
    assert response.status_code == 202
    created = response.json()

    await _upload_required_documents(
        test_client, auth_headers, created["id"], letter_text=letter_text, imaging_text=imaging_text,
    )
    return created


async def _generate_slots_for_doctor(test_client: AsyncClient, auth_headers, doctor_id: int) -> None:
    """Gives a real platform doctor at least one open, future ScheduleSlot —
    same two-step generation `test_schedule.py` uses (recurring
    DoctorAvailability -> materialized ScheduleSlot rows)."""
    await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": doctor_id, "weekday": 0, "start_time": "09:00", "end_time": "17:00", "slot_minutes": 30},
        headers=auth_headers,
    )
    await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": doctor_id, "days_ahead": 14}, headers=auth_headers,
    )


async def test_eligible_referral_pauses_for_specialist_approval_then_resumes_to_scheduled(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
    )

    fetched = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert fetched["status"] == "awaiting_specialist_approval"

    state = (await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)).json()
    assert len(state["specialist_candidates"]) >= 1
    assert all("reasons" in c and "score" in c for c in state["specialist_candidates"])
    candidate_id = state["specialist_candidates"][0]["doctor_id"]

    # a pcp-only user (no referral:approve) cannot resume the pause
    denied = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=auth_headers,
    )
    assert denied.status_code == 403

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=auth_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduled"

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "scheduled"
    # `specialist_id` is a real FK into our own `doctors` table; the mock
    # provider directory's doctor_id is a separate synthetic ID space with no
    # row there, so it deliberately isn't written back onto the referral.
    assert final["specialist_id"] is None

    notes = (await test_client.get(f"/referral/requests/{created['id']}/notes", headers=auth_headers)).json()
    assert len(notes) == 1

    assert notification_sent_messages[-1]["channel"] == "email"
    assert "confirmed" in notification_sent_messages[-1]["message"]


async def test_scheduling_with_no_available_slots_marks_delayed(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data, monkeypatch,
):
    """Phase 8: `scheduling_node`'s "no slots within target" branch — forces
    the mock scheduling system's own availability generator to return zero
    slots (a real, existing knob on that system, not a hack around it), so
    the referral lands on `scheduling_delayed` instead of `scheduled`."""
    from mock_systems.scheduling_mock import main as scheduling_mock_main

    monkeypatch.setattr(scheduling_mock_main, "MAX_SLOTS_RETURNED", 0)

    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
    )
    state = (await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)).json()
    candidate_id = state["specialist_candidates"][0]["doctor_id"]

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=auth_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduling_delayed"

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "scheduling_delayed"


async def test_ineligible_referral_pauses_but_wrong_resume_endpoint_rejects_it(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """Eligibility denial now pauses the graph (escalate_eligibility_node's
    interrupt()) rather than dead-ending it — but the specialist-approval
    /resume endpoint is guarded by referral status, so it still refuses a
    denied referral: that pause is for a different, more consequential
    decision. See test_care_coordinator_can_review_and_override_a_denied_referral
    for the actual review/override path."""
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="NOT-A-REAL-PLAN",
        reason="Persistent lower back pain",
    )

    fetched = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert fetched["status"] == "eligibility_denied"

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    response = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": 88}, headers=auth_headers,
    )
    assert response.status_code == 409


async def test_specialist_without_override_permission_cannot_override_denied_eligibility(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """referral:override is deliberately narrower than referral:approve —
    specialist holds the latter (can approve a candidate the workflow
    already surfaced) but not the former (bypassing a failed eligibility
    check is a bigger call)."""
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="NOT-A-REAL-PLAN",
        reason="Persistent lower back pain",
    )
    await _grant_role(test_session, test_user_data["username"], "specialist")
    response = await test_client.post(
        f"/referral-workflow/{created['id']}/override-eligibility",
        json={"comment": "Verified manually by phone"}, headers=auth_headers,
    )
    assert response.status_code == 403


async def test_care_coordinator_can_review_and_override_a_denied_referral(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """The full reported gap: a referral denied at eligibility now has a
    real review path for a care coordinator — comment (POST /notes),
    optionally attach a document (lands on both the referral AND the
    patient's own medical record), then override. Overriding resumes the
    workflow into recommend_specialist — the exact same modular step a
    normally-eligible referral reaches, not a separate parallel mechanism —
    so the referral can still be approved and scheduled the usual way
    afterward."""
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="NOT-A-REAL-PLAN",
        reason="Persistent lower back pain",
    )
    patient_id = created["patient_id"]
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")

    note = await test_client.post(
        f"/referral/requests/{created['id']}/notes",
        json={"note": "Called patient — coverage started this week, not yet synced with payer records."},
        headers=auth_headers,
    )
    assert note.status_code == 201

    upload = await test_client.post(
        f"/referral/requests/{created['id']}/documents", headers=auth_headers,
        files={"file": ("proof_of_coverage.pdf", b"dummy pdf bytes", "application/pdf")},
    )
    assert upload.status_code == 202

    records = (await test_client.get(f"/medical-records/?patient_id={patient_id}", headers=auth_headers)).json()
    assert any(r["record_type"] == "referral_document" for r in records["items"])

    override = await test_client.post(
        f"/referral-workflow/{created['id']}/override-eligibility",
        json={"comment": "Coverage confirmed by phone — proceeding."}, headers=auth_headers,
    )
    assert override.status_code == 200
    assert override.json()["status"] == "awaiting_specialist_approval"

    fetched = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert fetched["status"] == "awaiting_specialist_approval"

    notes = (await test_client.get(f"/referral/requests/{created['id']}/notes", headers=auth_headers)).json()
    assert any("Coverage confirmed by phone" in n["note"] for n in notes)

    # Overridden referral reaches the SAME recommend_specialist step and can
    # be approved/scheduled through the ordinary /resume endpoint.
    state = (await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)).json()
    assert len(state["specialist_candidates"]) >= 1
    candidate_id = state["specialist_candidates"][0]["doctor_id"]
    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=auth_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduled"

    # Trying to override again is refused — nothing left to override.
    second_override = await test_client.post(
        f"/referral-workflow/{created['id']}/override-eligibility",
        json={"comment": "again"}, headers=auth_headers,
    )
    assert second_override.status_code == 409


async def test_referral_with_preselected_specialist_books_immediately_without_specialist_approval(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """A patient (or staff) who already picked a real, bookable platform
    doctor via the unified "New Request" flow shouldn't have that choice
    ignored — this referral must book straight into that doctor's own
    ScheduleSlot table (book_real_appointment_node), skipping
    recommend_specialist/await_specialist_approval (the external-mock-
    directory path) entirely."""
    # care_coordinator first so this account can create the specialist
    # doctor and generate its slots (doctor:manage/appointment:manage) —
    # _submit_referral grants "pcp" on top of this afterward.
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    specialist_data = {**test_doctor_data, "first_name": "Priya", "email": "dr.priya@hospital.com",
                        "specialization": "Orthopedics", "license_number": "MD-SPECIALIST-1"}
    specialist = (await test_client.post("/doctors/", json=specialist_data, headers=auth_headers)).json()
    await _generate_slots_for_doctor(test_client, auth_headers, specialist["id"])

    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
        specialist_id=specialist["id"],
    )

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "scheduled"
    assert final["specialist_id"] == specialist["id"]

    appointments = (
        await test_client.get(f"/appointments/?doctor_id={specialist['id']}", headers=auth_headers)
    ).json()
    matches = [a for a in appointments["items"] if a["patient_id"] == created["patient_id"]]
    assert len(matches) == 1
    assert matches[0]["status"] == "scheduled"

    # Never paused for a human to pick a specialist — nothing to approve.
    denied = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": specialist["id"]}, headers=auth_headers,
    )
    assert denied.status_code == 409


async def test_referral_with_preselected_specialist_and_no_slots_marks_delayed_and_notifies_coordinator(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """Same pre-chosen-specialist path, but the doctor has zero open slots —
    there's no external-directory fallback on this path, so it must land on
    scheduling_delayed and page a care coordinator to schedule it manually,
    rather than silently stalling."""
    from sqlalchemy import select

    from app.models.notification import Notification

    # Granted first (see the sibling test above) — also who the "no slots"
    # notification should page.
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    specialist_data = {**test_doctor_data, "first_name": "Priya", "email": "dr.priya2@hospital.com",
                        "specialization": "Orthopedics", "license_number": "MD-SPECIALIST-2"}
    specialist = (await test_client.post("/doctors/", json=specialist_data, headers=auth_headers)).json()
    # Deliberately no availability/slots generated for this doctor.

    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
        specialist_id=specialist["id"],
    )

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "scheduling_delayed"

    notifications = (
        await test_session.execute(
            select(Notification).where(
                Notification.referral_id == created["id"],
                Notification.title == "No open slots for pre-selected specialist",
            )
        )
    ).scalars().all()
    assert len(notifications) >= 1


async def test_referral_with_preferred_slot_books_that_specific_slot_not_soonest(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """A requester who picked a specific slot (not just "soonest available")
    for their pre-chosen specialist should get exactly that slot —
    book_real_appointment_node must prefer it over find_soonest_open_slot's
    default when a valid preferred_slot_id is present."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    specialist_data = {**test_doctor_data, "first_name": "Priya", "email": "dr.priya3@hospital.com",
                        "specialization": "Orthopedics", "license_number": "MD-SPECIALIST-3"}
    specialist = (await test_client.post("/doctors/", json=specialist_data, headers=auth_headers)).json()
    await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": specialist["id"], "weekday": 0, "start_time": "09:00", "end_time": "12:00", "slot_minutes": 30},
        headers=auth_headers,
    )
    slots = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": specialist["id"], "days_ahead": 14}, headers=auth_headers
    )).json()
    slots.sort(key=lambda s: s["starts_at"])
    later_slot = slots[-1]  # deliberately NOT the soonest

    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
        specialist_id=specialist["id"],
        preferred_slot_id=later_slot["id"],
    )

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "scheduled"
    assert final["preferred_slot_id"] == later_slot["id"]

    booked_slot = (
        await test_client.get(f"/schedule/slots/?doctor_id={specialist['id']}&is_booked=true", headers=auth_headers)
    ).json()
    assert {s["id"] for s in booked_slot["items"]} == {later_slot["id"]}

    appointments = (
        await test_client.get(f"/appointments/?doctor_id={specialist['id']}", headers=auth_headers)
    ).json()
    matches = [a for a in appointments["items"] if a["patient_id"] == created["patient_id"]]
    assert len(matches) == 1
    assert matches[0]["appointment_datetime"] == later_slot["starts_at"]
