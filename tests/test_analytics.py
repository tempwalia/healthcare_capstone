"""Phase 11 — read-only analytics aggregation over real referral data."""
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import select

from app.models.outbox import OutboxEvent
from tests.test_referral import _grant_role
from tests.test_referral_outcome import _complete_referral_to_scheduled
from tests.test_referral_workflow_agents import _submit_referral, _upload_required_documents


async def test_analytics_requires_permission(test_client: AsyncClient, auth_headers):
    response = await test_client.get("/analytics/referrals/summary", headers=auth_headers)
    assert response.status_code == 403


async def test_referral_summary_reflects_real_data(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data, test_patient_data
):
    eligible = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
    )
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    state = (await test_client.get(f"/referral-workflow/{eligible['id']}/state", headers=auth_headers)).json()
    candidate_id = state["specialist_candidates"][0]["doctor_id"]
    await test_client.post(
        f"/referral-workflow/{eligible['id']}/resume", json={"doctor_id": candidate_id}, headers=auth_headers,
    )

    # A second patient referred to the same doctor, with an unrecognized
    # policy number — denies eligibility, same as `test_referral.py`'s
    # equivalent case. Explicit, rather than relying on `test_patient_data`
    # leaving insurance fields blank: patient creation now auto-assigns a
    # random demo policy when left blank (see app/services/insurance.py),
    # which would otherwise make this test's eligibility outcome a coin flip.
    other_patient = (await test_client.post(
        "/patients/",
        json={**test_patient_data, "email": "denied.patient@example.com", "insurance_policy_number": "NOT-A-REAL-PLAN"},
        headers=auth_headers,
    )).json()
    denied = (await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": other_patient["id"],
            "referring_doctor_id": eligible["referring_doctor_id"],
            "request_date": "2026-08-06",
            "reason": "Persistent lower back pain",
        },
        headers=auth_headers,
    )).json()
    await _upload_required_documents(test_client, auth_headers, denied["id"])

    response = await test_client.get("/analytics/referrals/summary", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["by_status"].get("scheduled", 0) >= 1
    assert body["by_status"].get("eligibility_denied", 0) >= 1
    assert body["avg_time_to_schedule_hours"] >= 0
    assert body["eligibility_denial_rate"] > 0
    assert any(s["specialty"] == "Orthopedics" for s in body["top_specialties_requested"])
    assert isinstance(body["delay_risk_referrals"], int)


async def test_avg_time_to_schedule_still_counts_referrals_that_later_completed(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    """Regression coverage for the fix to _avg_time_to_schedule_hours: it
    used to be computed from ReferralRequest.updated_at, which only holds the
    latest transition — so a referral that moved on to `completed` (by having
    its outcome recorded) would silently stop counting toward this metric
    even though it genuinely did get scheduled at some point. It's now
    computed from the durable outbox_events trail instead, so it must still
    count here."""
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )
    outcome_response = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers, json={"symptoms": "back pain"},
    )
    assert outcome_response.status_code == 202

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "completed"

    # Force a known, unambiguous gap between the two milestone events instead
    # of relying on real wall-clock elapsed time — SQLite's CURRENT_TIMESTAMP
    # (unlike Postgres's now()) only has second-level resolution, so a fast
    # in-memory test run can easily see both events land in the same second
    # and produce a coincidental 0.0 even with correct code.
    submitted_event = (
        await test_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.referral_id == created["id"], OutboxEvent.event_type == "referral.submitted"
            )
        )
    ).scalar_one()
    scheduled_event = (
        await test_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.referral_id == created["id"], OutboxEvent.event_type == "referral.appointment.scheduled"
            )
        )
    ).scalar_one()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    submitted_event.created_at = base
    scheduled_event.created_at = base + timedelta(hours=3)
    await test_session.commit()

    response = await test_client.get("/analytics/referrals/summary", headers=auth_headers)
    assert response.status_code == 200
    # The real assertion: this referral is `completed`, not `scheduled`, yet
    # its 3-hour scheduling delta is still counted. The old, buggy
    # `updated_at`-based code would instead report 0.0 (empty list) here,
    # since it only matched `status == "scheduled"`.
    assert response.json()["avg_time_to_schedule_hours"] == 3.0
    assert response.json()["by_status"].get("completed", 0) >= 1
