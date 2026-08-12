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
    """specialist can create the patient/doctor fixtures (needs
    patient:manage/doctor:manage) but deliberately lacks referral:create —
    submitting the referral itself must still be refused. (care_coordinator
    used to be this test's example role too, but now genuinely holds
    referral:create — see test_care_coordinator_can_submit_a_referral below
    — so it no longer fits this negative case.)"""
    await _grant_role(test_session, test_user_data["username"], "specialist")
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


async def test_care_coordinator_can_submit_a_referral(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """care_coordinator gained referral:create so a coordinator can start a
    referral through the exact same POST /referral/requests/ path a patient
    or PCP uses — the "same existing logic" the self-service/staff form
    split in static/js/modules/referrals.js already branches on, not a
    separate coordinator-only creation path."""
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
    assert response.status_code == 202


async def test_submit_referral_with_reason_proceeds_past_intake_without_documents(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """POC behavior: a filled-in Reason is sufficient on its own — the
    referral must not sit waiting on document uploads a patient may never
    provide. No documents are uploaded at submit time (they can't be — the
    background workflow starts the instant the referral exists, before a
    separate upload call could possibly happen); `intake_node` auto-attaches
    a random sample referral-letter/report pair when none exist yet, so the
    workflow proceeds straight to eligibility checking instead of pausing at
    `awaiting_documents`. An explicit unrecognized policy number keeps the
    eligibility outcome deterministic (patient creation now auto-assigns a
    random demo policy when left blank — see app/services/insurance.py, and
    would otherwise make this test's outcome a coin flip). Full happy-path
    coverage (eligible patient, specialist approval pause, resume,
    scheduling) lives in test_referral_workflow_agents.py."""
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post(
        "/patients/",
        json={**test_patient_data, "insurance_policy_number": "NOT-A-REAL-PLAN"},
        headers=auth_headers,
    )).json()
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
    assert fetched.json()["status"] == "eligibility_denied"

    # Never uploaded anything ourselves — these came from intake_node's
    # auto-sample fallback, clearly labeled so it's obvious in the UI too.
    documents = (
        await test_client.get(f"/referral/requests/{created['id']}/documents", headers=auth_headers)
    ).json()
    assert len(documents) == 2
    assert all(d["filename"].startswith("[auto-sample]") for d in documents)


async def test_submit_referral_with_no_reason_and_no_documents_still_proceeds(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """Even a referral with a blank reason and no uploaded documents must not
    dead-end: intake_node's auto-sample fallback gives it something real to
    extract from either way, so it still proceeds past `awaiting_documents`."""
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post(
        "/patients/",
        json={**test_patient_data, "insurance_policy_number": "NOT-A-REAL-PLAN"},
        headers=auth_headers,
    )).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await _link_doctor_to_user(test_session, doctor["id"], test_user_data["username"])

    response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"],
            "referring_doctor_id": doctor["id"],
            "request_date": "2026-08-06",
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    created = response.json()

    fetched = await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)
    assert fetched.json()["status"] != "awaiting_documents"


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


async def test_list_referrals_q_searches_reason_location_and_id(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """GET /referral/requests/?q= — matches reason/preferred_location text
    or an exact id, still within the caller's own referral_visibility_filter
    scope (care_coordinator here resolves to unrestricted, same as
    list_referrals without q — the scoping itself is covered elsewhere)."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    knee = (await test_client.post(
        "/referral/requests/",
        json={"patient_id": patient["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06",
              "reason": "Persistent knee pain after running"},
        headers=auth_headers,
    )).json()
    heart = (await test_client.post(
        "/referral/requests/",
        json={"patient_id": patient["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06",
              "reason": "Chest tightness and palpitations", "preferred_location": "Downtown Clinic"},
        headers=auth_headers,
    )).json()

    by_reason = (await test_client.get("/referral/requests/?q=knee", headers=auth_headers)).json()
    assert {r["id"] for r in by_reason["items"]} == {knee["id"]}

    by_location = (await test_client.get("/referral/requests/?q=Downtown", headers=auth_headers)).json()
    assert {r["id"] for r in by_location["items"]} == {heart["id"]}

    by_id = (await test_client.get(f"/referral/requests/?q={knee['id']}", headers=auth_headers)).json()
    assert {r["id"] for r in by_id["items"]} == {knee["id"]}

    no_match = (await test_client.get("/referral/requests/?q=nonexistent-xyz", headers=auth_headers)).json()
    assert no_match["items"] == []


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

    # This referral was submitted with neither a reason nor a document, so
    # intake_node had already auto-attached a random sample document pair
    # (filenames prefixed "[auto-sample]") by the time this upload landed —
    # 2 auto-attached + the 1 uploaded here.
    listed = (await test_client.get(f"/referral/requests/{referral['id']}/documents", headers=auth_headers)).json()
    assert len(listed) == 3
    own_upload = next(d for d in listed if d["filename"] == "referral_letter.txt")
    assert own_upload is not None
    assert sum(1 for d in listed if d["filename"].startswith("[auto-sample]")) == 2
