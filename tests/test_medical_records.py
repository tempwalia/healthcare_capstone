"""Medical record create/update/delete ownership scoping and the
patient/care_coordinator medical_record:manage grant — "at every stage
either user or care coordinator or doctor should have the option to add and
delete the patient medical records" (explicit user request). Companion to
`tests/test_record_scope.py`'s GET-side scoping coverage; this file covers
the mutation routes, which previously had zero ownership scoping at all —
see app/api/routes/medical_records.py."""
from httpx import AsyncClient

from tests.test_record_scope import _reset_roles
from tests.test_referral import _grant_role, _link_doctor_to_user, _link_patient_to_user


async def test_patient_can_create_update_delete_their_own_medical_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient["id"], test_user_data["username"])

    created = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z",
              "record_type": "lab_result", "diagnosis": "Self-reported cholesterol panel"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    record = created.json()

    updated = await test_client.put(
        f"/medical-records/{record['id']}", json={"notes": "Updated by patient"}, headers=auth_headers
    )
    assert updated.status_code == 200
    assert updated.json()["notes"] == "Updated by patient"

    deleted = await test_client.delete(f"/medical-records/{record['id']}", headers=auth_headers)
    assert deleted.status_code == 200


async def test_patient_cannot_create_or_write_another_patients_medical_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient_a = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    other_data = {**test_patient_data, "email": "other.patient@example.com"}
    patient_b = (await test_client.post("/patients/", json=other_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_record = (await test_client.post(
        "/medical-records/",
        json={"patient_id": patient_b["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient_a["id"], test_user_data["username"])

    blocked_create = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient_b["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-02T09:00:00Z"},
        headers=auth_headers,
    )
    assert blocked_create.status_code == 403

    blocked_update = await test_client.put(
        f"/medical-records/{other_record['id']}", json={"notes": "should not work"}, headers=auth_headers
    )
    assert blocked_update.status_code == 404  # scoped lookup — not a leak, looks like it doesn't exist

    blocked_delete = await test_client.delete(f"/medical-records/{other_record['id']}", headers=auth_headers)
    assert blocked_delete.status_code == 404


async def test_specialist_cannot_author_a_record_under_another_doctors_name(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """medical_record:manage no longer means "attribute this record to any
    doctor" — a view_own-scoped caller (pcp/specialist) must write under
    their own linked doctor_id."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor_a = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_doctor_data = {**test_doctor_data, "email": "other.specialist@example.com", "license_number": "MD-OTHER-1"}
    doctor_b = (await test_client.post("/doctors/", json=other_doctor_data, headers=auth_headers)).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "specialist")
    await _link_doctor_to_user(test_session, doctor_a["id"], test_user_data["username"])

    blocked = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor_b["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )
    assert blocked.status_code == 403

    allowed = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor_a["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )
    assert allowed.status_code == 201


async def test_create_medical_record_without_doctor_id_succeeds(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data,
):
    """doctor_id is now optional on MedicalRecord — a patient's own direct
    document upload has no treating doctor to attribute it to."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()

    created = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "visit_date": "2026-07-01T09:00:00Z", "record_type": "self_upload"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    assert created.json()["doctor_id"] is None


async def test_patient_can_quick_upload_a_document_creating_a_new_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data,
):
    """POST /medical-records/quick-upload — the "just upload a document,
    don't make me pick a doctor or an existing record first" path patients
    use directly, and that the unified New Request flow's inline upload
    button also calls."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient["id"], test_user_data["username"])

    uploaded = await test_client.post(
        "/medical-records/quick-upload",
        headers=auth_headers,
        data={"patient_id": str(patient["id"]), "record_type": "lab_result"},
        files={"file": ("bloodwork.pdf", b"dummy pdf bytes", "application/pdf")},
    )
    assert uploaded.status_code == 201
    document = uploaded.json()
    assert document["filename"] == "bloodwork.pdf"

    records = (await test_client.get(f"/medical-records/?patient_id={patient['id']}", headers=auth_headers)).json()
    match = next(r for r in records["items"] if r["id"] == document["medical_record_id"])
    assert match["doctor_id"] is None
    assert match["record_type"] == "lab_result"

    documents = (
        await test_client.get(f"/medical-records/{document['medical_record_id']}/documents", headers=auth_headers)
    ).json()
    assert any(d["filename"] == "bloodwork.pdf" for d in documents)


async def test_quick_upload_rejects_upload_for_another_patient_without_view_all(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data,
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient_a = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    other_data = {**test_patient_data, "email": "other.upload@example.com"}
    patient_b = (await test_client.post("/patients/", json=other_data, headers=auth_headers)).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient_a["id"], test_user_data["username"])

    blocked = await test_client.post(
        "/medical-records/quick-upload",
        headers=auth_headers,
        data={"patient_id": str(patient_b["id"])},
        files={"file": ("bloodwork.pdf", b"dummy pdf bytes", "application/pdf")},
    )
    assert blocked.status_code == 403


async def test_attach_document_to_existing_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data,
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    record = (await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )).json()

    uploaded = await test_client.post(
        f"/medical-records/{record['id']}/documents",
        headers=auth_headers,
        files={"file": ("mri_report.pdf", b"dummy pdf bytes", "application/pdf")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["medical_record_id"] == record["id"]

    documents = (
        await test_client.get(f"/medical-records/{record['id']}/documents", headers=auth_headers)
    ).json()
    assert any(d["filename"] == "mri_report.pdf" for d in documents)


async def test_care_coordinator_can_add_and_delete_medical_records(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """care_coordinator gained medical_record:manage (was view-only) per
    explicit request — coordinators can now add/delete records for any
    patient, same broad-access shape as their existing patient:manage."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    created = await test_client.post(
        "/medical-records/",
        json={"patient_id": patient["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z",
              "record_type": "prescription"},
        headers=auth_headers,
    )
    assert created.status_code == 201

    deleted = await test_client.delete(f"/medical-records/{created.json()['id']}", headers=auth_headers)
    assert deleted.status_code == 200
