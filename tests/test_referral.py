from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.seed import seed_roles_and_permissions
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.role import Role
from app.models.user import User


async def _grant_role(test_session, username: str, role_name: str) -> None:
    await seed_roles_and_permissions(test_session)
    user = (
        await test_session.execute(
            select(User).options(selectinload(User.roles)).where(User.username == username)
        )
    ).scalar_one()
    role = (await test_session.execute(select(Role).where(Role.name == role_name))).scalar_one()
    user.roles.append(role)
    await test_session.commit()


async def _link_patient_to_user(test_session, patient_id: int, username: str) -> None:
    user = (await test_session.execute(select(User).where(User.username == username))).scalar_one()
    patient = await test_session.get(Patient, patient_id)
    patient.user_id = user.id
    await test_session.commit()


async def _link_doctor_to_user(test_session, doctor_id: int, username: str) -> None:
    user = (await test_session.execute(select(User).where(User.username == username))).scalar_one()
    doctor = await test_session.get(Doctor, doctor_id)
    doctor.user_id = user.id
    await test_session.commit()


async def test_submit_referral_requires_referral_create_permission(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """care_coordinator can create the patient/doctor fixtures (needs
    patient:manage/doctor:manage) but deliberately lacks referral:create —
    submitting the referral itself must still be refused."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"],
            "referring_doctor_id": doctor["id"],
            "request_date": "2026-08-06",
            "reason": "Persistent lower back pain",
        },
        headers=auth_headers,
    )
    assert response.status_code == 403


async def test_submit_referral_pauses_for_documents_then_denies_eligibility(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """No documents are uploaded at submit time (they can't be — the
    background workflow starts the instant the referral exists, before a
    separate upload call could possibly happen), so the real workflow pauses
    at `awaiting_documents` first; uploading the two required document types
    unblocks it. `test_patient_data` has no `insurance_policy_number` on
    file, so it then denies eligibility. Full happy-path coverage (eligible
    patient, specialist approval pause, resume, scheduling) lives in
    test_referral_workflow_agents.py."""
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await _link_doctor_to_user(test_session, doctor["id"], test_user_data["username"])

    response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"],
            "referring_doctor_id": doctor["id"],
            "request_date": "2026-08-06",
            "reason": "Persistent lower back pain",
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    created = response.json()
    assert created["status"] == "submitted"
    assert created["workflow_thread_id"] == f"referral-{created['id']}"

    fetched = await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)
    assert fetched.json()["status"] == "awaiting_documents"

    await test_client.post(
        f"/referral/requests/{created['id']}/documents", headers=auth_headers,
        files={"file": ("referral_letter.txt", b"Referral letter body.", "text/plain")},
    )
    still_missing_imaging = await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)
    assert still_missing_imaging.json()["status"] == "awaiting_documents"

    await test_client.post(
        f"/referral/requests/{created['id']}/documents", headers=auth_headers,
        files={"file": ("mri_report.txt", b"MRI imaging report body.", "text/plain")},
    )
    fully_uploaded = await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)
    assert fully_uploaded.json()["status"] == "eligibility_denied"


async def test_submit_referral_rejects_unknown_patient(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": 9999,
            "referring_doctor_id": doctor["id"],
            "request_date": "2026-08-06",
        },
        headers=auth_headers,
    )
    assert response.status_code == 404


async def test_patient_sees_only_their_own_referral(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient_a = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    other_patient_data = {**test_patient_data, "email": "other@example.com"}
    patient_b = (await test_client.post("/patients/", json=other_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    referral_a = (await test_client.post(
        "/referral/requests/",
        json={"patient_id": patient_a["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06"},
        headers=auth_headers,
    )).json()
    await test_client.post(
        "/referral/requests/",
        json={"patient_id": patient_b["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06"},
        headers=auth_headers,
    )

    # re-scope this same user to *patient* visibility, linked only to patient_a
    user = (await test_session.execute(select(User).options(selectinload(User.roles)).where(User.username == test_user_data["username"]))).scalar_one()
    user.roles.clear()
    await test_session.commit()
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient_a["id"], test_user_data["username"])

    listed = (await test_client.get("/referral/requests/", headers=auth_headers)).json()
    ids = {r["id"] for r in listed["items"]}
    assert referral_a["id"] in ids
    assert len(ids) == 1  # patient_b's referral must not be visible


async def test_care_coordinator_sees_all_referrals(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await test_client.post(
        "/referral/requests/",
        json={"patient_id": patient["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06"},
        headers=auth_headers,
    )

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    listed = (await test_client.get("/referral/requests/", headers=auth_headers)).json()
    assert listed["total"] >= 1


async def test_upload_and_list_referral_document(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await _link_doctor_to_user(test_session, doctor["id"], test_user_data["username"])
    referral = (await test_client.post(
        "/referral/requests/",
        json={"patient_id": patient["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06"},
        headers=auth_headers,
    )).json()

    upload = await test_client.post(
        f"/referral/requests/{referral['id']}/documents",
        headers=auth_headers,
        files={"file": ("referral_letter.txt", b"Patient reports lower back pain.", "text/plain")},
    )
    assert upload.status_code == 202
    assert upload.json()["extraction_status"] == "queued"

    listed = await test_client.get(f"/referral/requests/{referral['id']}/documents", headers=auth_headers)
    assert len(listed.json()) == 1
    assert listed.json()[0]["filename"] == "referral_letter.txt"
