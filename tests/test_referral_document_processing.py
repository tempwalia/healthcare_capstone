"""Phase 7 — document text extraction (diagnosis/procedure codes) and the
referral-history summary that draws on the patient's prior medical records."""
from httpx import AsyncClient

from app.agents.nodes import intake as intake_module
from tests.test_referral import _grant_role, _link_doctor_to_user
from tests.test_referral_workflow_agents import _submit_referral


async def test_document_upload_extracts_diagnosis_and_procedure_codes(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data, monkeypatch,
):
    """A real, uploaded-after-the-fact document can no longer be the thing
    under test here: with documents now optional (a filled-in Reason alone
    is sufficient — see test_referral.py), `intake_node` runs to completion
    the instant the referral is submitted, before a separate upload call
    could possibly land. Since no document exists yet at that point (this
    referral is deliberately submitted with no reason and no upload), this
    exercises `intake_node`'s auto-sample fallback instead — pinned to the
    orthopedics pair (real ICD-10/CPT codes, see sample_documents/) so the
    extracted codes are deterministic rather than one of 3 random pairs."""
    monkeypatch.setattr(
        intake_module,
        "_SAMPLE_DOCUMENT_PAIRS",
        [("orthopedics_referral_letter.txt", "orthopedics_mri_imaging_report.txt")],
    )
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post(
        "/patients/",
        json={
            "first_name": "Jamie", "last_name": "Rivera", "email": "jamie.rivera@example.com",
            "phone": "+15550001111", "date_of_birth": "1985-04-12", "gender": "female",
            "insurance_provider": "Acme Health", "insurance_policy_number": "ACME-991123",
        },
        headers=auth_headers,
    )).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await _link_doctor_to_user(test_session, doctor["id"], test_user_data["username"])

    response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06",
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    created = response.json()

    documents = (
        await test_client.get(f"/referral/requests/{created['id']}/documents", headers=auth_headers)
    ).json()
    assert len(documents) == 2
    assert all(d["filename"].startswith("[auto-sample]") for d in documents)
    letter = next(d for d in documents if "referral_letter" in d["filename"])
    assert letter["extraction_status"] == "complete"
    assert "M54.5" in letter["extracted_diagnosis_codes"]
    assert "72148" in letter["extracted_procedure_codes"]

    state = (
        await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)
    ).json()
    assert "M54.5" in state["diagnosis_codes"]
    assert "72148" in state["procedure_codes"]
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
