"""Phase 7 — document text extraction (diagnosis/procedure codes) and the
referral-history summary that draws on the patient's prior medical records."""
from httpx import AsyncClient

from tests.test_referral import _grant_role
from tests.test_referral_workflow_agents import _submit_referral


async def test_document_upload_extracts_diagnosis_and_procedure_codes(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain",
        letter_text="Patient presents with low back pain, diagnosis M54.5, referred for procedure 99213.",
        imaging_text="MRI imaging report: mild disc bulge, no acute findings.",
    )

    documents = (
        await test_client.get(f"/referral/requests/{created['id']}/documents", headers=auth_headers)
    ).json()
    letter = next(d for d in documents if d["filename"] == "referral_letter.txt")
    assert letter["extraction_status"] == "complete"
    assert "M54.5" in letter["extracted_diagnosis_codes"]
    assert "99213" in letter["extracted_procedure_codes"]

    state = (
        await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)
    ).json()
    assert "M54.5" in state["diagnosis_codes"]
    assert "99213" in state["procedure_codes"]
    assert state["missing_documents"] == []


async def test_specialist_note_includes_prior_medical_history(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
    )

    # A prior visit on file for this patient — the summary should draw on it.
    await test_client.post(
        "/medical-records/",
        headers=auth_headers,
        json={
            "patient_id": created["patient_id"],
            "doctor_id": created["referring_doctor_id"],
            "visit_date": "2026-05-01T09:00:00Z",
            "diagnosis": "Type 2 Diabetes Mellitus",
            "treatment": "Metformin",
        },
    )

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    state = (
        await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)
    ).json()
    candidate_id = state["specialist_candidates"][0]["doctor_id"]
    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=auth_headers,
    )
    assert resumed.status_code == 200

    notes = (
        await test_client.get(f"/referral/requests/{created['id']}/notes", headers=auth_headers)
    ).json()
    assert len(notes) == 1
    assert "Type 2 Diabetes Mellitus" in notes[0]["note"]
    assert "Persistent lower back pain" in notes[0]["note"]
