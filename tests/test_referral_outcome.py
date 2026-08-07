"""Post-referral consult outcome capture (symptoms/diagnosis/prescription)
and the resulting whole-care-journey completion summary."""
from httpx import AsyncClient

from tests.test_referral import _grant_role
from tests.test_referral_workflow_agents import _submit_referral


async def _complete_referral_to_scheduled(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
) -> dict:
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
    assert resumed.json()["status"] == "scheduled"
    return created


async def test_recording_outcome_requires_permission(test_client: AsyncClient, auth_headers):
    response = await test_client.post(
        "/referral/requests/9999/outcome", headers=auth_headers, json={"symptoms": "back pain"},
    )
    assert response.status_code == 403


async def test_recording_outcome_completes_referral_and_generates_summary(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )

    response = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers,
        json={
            "symptoms": "Lower back pain, limited mobility",
            "diagnosis": "Lumbar disc herniation",
            "prescription": "Naproxen 500mg twice daily",
            "follow_up_notes": "Follow up in 4 weeks; consider physical therapy",
        },
    )
    assert response.status_code == 202
    assert response.json()["referral_request_id"] == created["id"]

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "completed"

    outcome = (
        await test_client.get(f"/referral/requests/{created['id']}/outcome", headers=auth_headers)
    ).json()
    assert outcome["interaction_summary"] is not None
    assert "Naproxen" in outcome["interaction_summary"]
    assert "Lumbar disc herniation" in outcome["interaction_summary"]


async def test_recording_outcome_twice_conflicts(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )

    first = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers, json={"symptoms": "back pain"},
    )
    assert first.status_code == 202

    second = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers, json={"symptoms": "back pain again"},
    )
    assert second.status_code == 409


async def test_recording_outcome_writes_a_followup_medical_record_for_the_patient(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """The whole point of generate_completion_summary writing a MedicalRecord
    (not just ReferralOutcome.interaction_summary) is for the consult
    summary to show up on the patient's own chart for the next follow-up —
    verified here via GET /patients/{id}/context, the same aggregation the
    Patient Detail page renders from."""
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )
    await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers,
        json={
            "symptoms": "Lower back pain, limited mobility",
            "diagnosis": "Lumbar disc herniation",
            "prescription": "Naproxen 500mg twice daily",
            "follow_up_notes": "Follow up in 4 weeks; consider physical therapy",
        },
    )

    context = (
        await test_client.get(f"/patients/{created['patient_id']}/context", headers=auth_headers)
    ).json()
    consult_records = [r for r in context["medical_records"] if r["record_type"] == "referral_consult"]
    assert len(consult_records) == 1
    record = consult_records[0]
    assert record["doctor_id"] == created["referring_doctor_id"]
    assert record["diagnosis"] == "Lumbar disc herniation"
    assert record["prescription"] == "Naproxen 500mg twice daily"
    assert record["treatment"] == "Follow up in 4 weeks; consider physical therapy"
    assert record["notes"]  # the generated whole-care-journey summary, non-empty


async def test_doctor_role_can_resume_and_complete_a_referral_it_has_no_tie_to(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """The "doctor" role exists for exactly this POC gap: there's no real
    specialist login tied to the referral (the specialist_id on the referral
    stays whatever it was at submission — see submit_referral's own note),
    so a doctor-role account needs to be able to pick up and complete *any*
    pending referral platform-wide, not just ones it's linked to."""
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
    )
    state = (await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)).json()
    candidate_id = state["specialist_candidates"][0]["doctor_id"]

    await test_client.post(
        "/auth/register",
        json={"email": "doc.bystander@example.com", "username": "doc_bystander", "password": "testpassword123"},
    )
    await _grant_role(test_session, "doc_bystander", "doctor")
    login = await test_client.post(
        "/auth/login", data={"username": "doc_bystander", "password": "testpassword123"},
    )
    doctor_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=doctor_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduled"

    outcome_response = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=doctor_headers,
        json={"symptoms": "Lower back pain", "diagnosis": "Lumbar strain", "prescription": "Ibuprofen"},
    )
    assert outcome_response.status_code == 202

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=doctor_headers)).json()
    assert final["status"] == "completed"


async def test_outcome_visibility_matches_referral_visibility(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """Outcome visibility was previously staff-only (_get_staff_scoped_referral,
    now removed) even though a patient could already see the referral itself.
    It now reuses the exact same scope as the referral (_get_scoped_referral):
    the patient it belongs to, the referring doctor, and staff can all read
    the recorded consult outcome/summary; a bystander with no tie to the
    referral still can't (same 404 the referral itself already gives them)."""
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )
    await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers,
        json={"symptoms": "back pain", "diagnosis": "disc herniation", "prescription": "NSAIDs"},
    )

    # the referring doctor / care coordinator account (test_user_data) can see it
    staff_view = await test_client.get(f"/referral/requests/{created['id']}/outcome", headers=auth_headers)
    assert staff_view.status_code == 200

    # the patient (linked as "jamie_login" inside _submit_referral) can now
    # see the outcome too, same as they already could the referral itself
    await _grant_role(test_session, "jamie_login", "patient")
    patient_login = await test_client.post(
        "/auth/login", data={"username": "jamie_login", "password": "testpassword123"},
    )
    patient_headers = {"Authorization": f"Bearer {patient_login.json()['access_token']}"}

    patient_view = await test_client.get(f"/referral/requests/{created['id']}/outcome", headers=patient_headers)
    assert patient_view.status_code == 200
    assert patient_view.json()["diagnosis"] == "disc herniation"

    # a bystander patient with no tie to this referral still can't see it
    # (or the referral itself) — 404, not a leak
    await test_client.post(
        "/auth/register",
        json={"email": "bystander@example.com", "username": "bystander_patient", "password": "testpassword123"},
    )
    await _grant_role(test_session, "bystander_patient", "patient")
    bystander_login = await test_client.post(
        "/auth/login", data={"username": "bystander_patient", "password": "testpassword123"},
    )
    bystander_headers = {"Authorization": f"Bearer {bystander_login.json()['access_token']}"}

    cannot_see_referral = await test_client.get(f"/referral/requests/{created['id']}", headers=bystander_headers)
    assert cannot_see_referral.status_code == 404

    cannot_see_outcome = await test_client.get(
        f"/referral/requests/{created['id']}/outcome", headers=bystander_headers
    )
    assert cannot_see_outcome.status_code == 404


async def test_patient_context_surfaces_referral_documents(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """_submit_referral (via _complete_referral_to_scheduled) uploads a letter
    + imaging document as part of intake — GET /patients/{id}/context should
    surface both to the patient the referral belongs to, so a completed
    referral's supporting lab/imaging documents are visible on their own
    chart, not just to staff browsing the referral directly."""
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )

    await _grant_role(test_session, "jamie_login", "patient")
    patient_login = await test_client.post(
        "/auth/login", data={"username": "jamie_login", "password": "testpassword123"},
    )
    patient_headers = {"Authorization": f"Bearer {patient_login.json()['access_token']}"}

    context = (
        await test_client.get(f"/patients/{created['patient_id']}/context", headers=patient_headers)
    ).json()
    # If intake ran before an upload landed, the workflow may also have
    # auto-attached its own sample pair (see intake_node) alongside the two
    # real uploads below — assert the real uploads are present rather than
    # an exact set, and that everything returned belongs to this referral.
    filenames = {d["filename"] for d in context["documents"]}
    assert {"referral_letter.txt", "recent_imaging_report.txt"} <= filenames
    assert all(d["referral_request_id"] == created["id"] for d in context["documents"])
