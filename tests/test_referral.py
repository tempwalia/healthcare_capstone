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


async def _reset_roles(test_session, username: str) -> None:
    user = (
        await test_session.execute(
            select(User).options(selectinload(User.roles)).where(User.username == username)
        )
    ).scalar_one()
    user.roles.clear()
    await test_session.commit()


async def _register_and_login(test_client: AsyncClient, username: str) -> dict:
    """A genuinely separate account, not just a role-swap on the shared test
    user — needed whenever a test links two different Doctor rows to two
    different callers in the same test (Doctor.user_id is unique, so the
    shared test user can only ever be linked to one Doctor row at a time)."""
    await test_client.post(
        "/auth/register",
        json={"email": f"{username}@example.com", "username": username, "password": "testpassword123"},
    )
    login = await test_client.post("/auth/login", data={"username": username, "password": "testpassword123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


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


async def test_submit_referral_with_medical_record_id_validates_ownership(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    # care_coordinator (not pcp): needs medical_record:view_all to create
    # records for patients it isn't itself linked to as the treating doctor.
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient_a = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    other_data = {**test_patient_data, "email": "other.record@example.com"}
    patient_b = (await test_client.post("/patients/", json=other_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    record_b = (await test_client.post(
        "/medical-records/",
        json={"patient_id": patient_b["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )).json()

    mismatched = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient_a["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06",
            "medical_record_id": record_b["id"],
        },
        headers=auth_headers,
    )
    assert mismatched.status_code == 400

    record_a = (await test_client.post(
        "/medical-records/",
        json={"patient_id": patient_a["id"], "doctor_id": doctor["id"], "visit_date": "2026-07-01T09:00:00Z"},
        headers=auth_headers,
    )).json()
    matched = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient_a["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06",
            "medical_record_id": record_a["id"],
        },
        headers=auth_headers,
    )
    assert matched.status_code == 202
    assert matched.json()["medical_record_id"] == record_a["id"]


async def test_submit_referral_preferred_slot_id_requires_specialist_id(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await test_client.post(
        "/schedule/availability/",
        json={"doctor_id": doctor["id"], "weekday": 0, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30},
        headers=auth_headers,
    )
    slots = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": doctor["id"], "days_ahead": 14}, headers=auth_headers
    )).json()

    response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06",
            "preferred_slot_id": slots[0]["id"],
        },
        headers=auth_headers,
    )
    assert response.status_code == 400


async def test_submit_referral_preferred_slot_id_validates_doctor_and_availability(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    referring_doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    specialist_data = {**test_doctor_data, "email": "slot.specialist@example.com", "license_number": "MD-SLOT-1"}
    specialist = (await test_client.post("/doctors/", json=specialist_data, headers=auth_headers)).json()
    other_data = {**test_doctor_data, "email": "slot.other@example.com", "license_number": "MD-SLOT-2"}
    other_doctor = (await test_client.post("/doctors/", json=other_data, headers=auth_headers)).json()

    for d in (specialist, other_doctor):
        await test_client.post(
            "/schedule/availability/",
            json={"doctor_id": d["id"], "weekday": 0, "start_time": "09:00", "end_time": "12:00", "slot_minutes": 30},
            headers=auth_headers,
        )
    specialist_slots = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": specialist["id"], "days_ahead": 14}, headers=auth_headers
    )).json()
    other_slots = (await test_client.post(
        "/schedule/slots/generate", json={"doctor_id": other_doctor["id"], "days_ahead": 14}, headers=auth_headers
    )).json()

    # A slot that belongs to a different doctor than the chosen specialist
    wrong_doctor = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"], "referring_doctor_id": referring_doctor["id"],
            "specialist_id": specialist["id"], "preferred_slot_id": other_slots[0]["id"],
            "request_date": "2026-08-06",
        },
        headers=auth_headers,
    )
    assert wrong_doctor.status_code == 400

    # A slot that's already booked
    await test_client.post(
        f"/schedule/slots/{specialist_slots[0]['id']}/book",
        json={"patient_id": patient["id"]},
        headers=auth_headers,
    )
    already_booked = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"], "referring_doctor_id": referring_doctor["id"],
            "specialist_id": specialist["id"], "preferred_slot_id": specialist_slots[0]["id"],
            "request_date": "2026-08-06",
        },
        headers=auth_headers,
    )
    assert already_booked.status_code == 409

    # A genuinely open slot belonging to the chosen specialist succeeds
    valid = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"], "referring_doctor_id": referring_doctor["id"],
            "specialist_id": specialist["id"], "preferred_slot_id": specialist_slots[1]["id"],
            "request_date": "2026-08-06",
        },
        headers=auth_headers,
    )
    assert valid.status_code == 202
    assert valid.json()["preferred_slot_id"] == specialist_slots[1]["id"]


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

    # Every referral document also lands on the patient's own medical record
    # — "attached to the specific referral AND the patient record", not just
    # filed under the referral.
    records = (await test_client.get(f"/medical-records/?patient_id={patient['id']}", headers=auth_headers)).json()
    matches = [
        r for r in records["items"]
        if r["record_type"] == "referral_document" and "referral_letter.txt" in (r.get("notes") or "")
    ]
    assert len(matches) == 1


async def test_download_referral_document_requires_referral_visibility(
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
    document = (await test_client.post(
        f"/referral/requests/{referral['id']}/documents",
        headers=auth_headers,
        files={"file": ("referral_letter.txt", b"Patient reports lower back pain.", "text/plain")},
    )).json()

    downloaded = await test_client.get(
        f"/referral/requests/{referral['id']}/documents/{document['id']}/download", headers=auth_headers,
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"Patient reports lower back pain."

    # An unrelated specialist (not the referring doctor, not the specialist
    # on this referral) can't see the referral at all, so can't download. A
    # genuinely separate account — Doctor.user_id is unique, so the shared
    # test user (already linked to `doctor` above) can't also link to a
    # second doctor.
    other_data = {**test_doctor_data, "email": "unrelated.specialist@example.com", "license_number": "MD-UNREL-1"}
    other_doctor = (await test_client.post("/doctors/", json=other_data, headers=auth_headers)).json()
    other_headers = await _register_and_login(test_client, "unrelated_specialist_download")
    await _grant_role(test_session, "unrelated_specialist_download", "specialist")
    await _link_doctor_to_user(test_session, other_doctor["id"], "unrelated_specialist_download")

    blocked = await test_client.get(
        f"/referral/requests/{referral['id']}/documents/{document['id']}/download", headers=other_headers,
    )
    assert blocked.status_code == 404


async def test_referral_attached_record_404_when_none_attached(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    referral = (await test_client.post(
        "/referral/requests/",
        json={"patient_id": patient["id"], "referring_doctor_id": doctor["id"], "request_date": "2026-08-06"},
        headers=auth_headers,
    )).json()

    response = await test_client.get(f"/referral/requests/{referral['id']}/attached-record", headers=auth_headers)
    assert response.status_code == 404


async def test_referral_attached_record_visible_to_assigned_specialist_not_owning_the_record(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_patient_data, test_doctor_data
):
    """The gap: a patient's own doctor-less document upload (doctor_id=None)
    is invisible to medical_record_visibility_filter's normal ownership
    check for any specialist — but a specialist actually assigned to a
    referral that attached this record should still be able to open it and
    its documents. See app.services.document_access."""
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    patient = (await test_client.post("/patients/", json=test_patient_data, headers=auth_headers)).json()
    referring_doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    specialist_data = {**test_doctor_data, "email": "assigned.specialist@example.com", "license_number": "MD-ASSIGN-1"}
    specialist = (await test_client.post("/doctors/", json=specialist_data, headers=auth_headers)).json()
    unrelated_data = {**test_doctor_data, "email": "unrelated.doc2@example.com", "license_number": "MD-UNREL-2"}
    unrelated = (await test_client.post("/doctors/", json=unrelated_data, headers=auth_headers)).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "patient")
    await _link_patient_to_user(test_session, patient["id"], test_user_data["username"])
    uploaded = (await test_client.post(
        "/medical-records/quick-upload",
        headers=auth_headers,
        data={"patient_id": str(patient["id"]), "record_type": "lab_result"},
        files={"file": ("bloodwork.pdf", b"lab results content", "application/pdf")},
    )).json()

    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    referral = (await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"], "referring_doctor_id": referring_doctor["id"],
            "specialist_id": specialist["id"], "medical_record_id": uploaded["medical_record_id"],
            "request_date": "2026-08-06",
        },
        headers=auth_headers,
    )).json()

    # The assigned specialist — not the record's own doctor_id (None) — can
    # see the attached record and download its document.
    await _reset_roles(test_session, test_user_data["username"])
    await _grant_role(test_session, test_user_data["username"], "specialist")
    await _link_doctor_to_user(test_session, specialist["id"], test_user_data["username"])

    attached = await test_client.get(f"/referral/requests/{referral['id']}/attached-record", headers=auth_headers)
    assert attached.status_code == 200
    body = attached.json()
    assert body["record"]["id"] == uploaded["medical_record_id"]
    assert len(body["documents"]) == 1
    document_id = body["documents"][0]["id"]

    downloaded = await test_client.get(f"/medical-records/documents/{document_id}/download", headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"lab results content"

    # An unrelated specialist (not referring, not assigned) gets 404 on
    # both — a separate account, since the shared test user is already
    # linked to `specialist`'s doctor row (Doctor.user_id is unique).
    unrelated_headers = await _register_and_login(test_client, "unrelated_specialist_attached")
    await _grant_role(test_session, "unrelated_specialist_attached", "specialist")
    await _link_doctor_to_user(test_session, unrelated["id"], "unrelated_specialist_attached")

    blocked_record = await test_client.get(f"/referral/requests/{referral['id']}/attached-record", headers=unrelated_headers)
    assert blocked_record.status_code == 404
    blocked_download = await test_client.get(f"/medical-records/documents/{document_id}/download", headers=unrelated_headers)
    assert blocked_download.status_code == 404


async def test_notes_require_coordinator_specialist_or_admin_permission(
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

    # pcp alone (referral:create, not referral:approve/override) can't comment.
    denied = await test_client.post(
        f"/referral/requests/{referral['id']}/notes", json={"note": "should be refused"}, headers=auth_headers,
    )
    assert denied.status_code == 403

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    created_note = await test_client.post(
        f"/referral/requests/{referral['id']}/notes", json={"note": "Reviewed — looks fine."}, headers=auth_headers,
    )
    assert created_note.status_code == 201
    assert created_note.json()["note"] == "Reviewed — looks fine."

    listed = (await test_client.get(f"/referral/requests/{referral['id']}/notes", headers=auth_headers)).json()
    assert any(n["note"] == "Reviewed — looks fine." for n in listed)
