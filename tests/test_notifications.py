"""Workstream E — in-app notification center: self-scoped inbox (own-only,
never another user's) plus the trigger points wired into the referral
workflow (appointment scheduled -> notify patient; outcome recorded ->
notify referring doctor; awaiting specialist approval -> notify every
care_coordinator; eligibility denied -> notify referring doctor)."""
from httpx import AsyncClient

from tests.test_referral import _grant_role
from tests.test_referral_outcome import _complete_referral_to_scheduled
from tests.test_referral_workflow_agents import _submit_referral


async def test_notifications_list_is_empty_for_a_fresh_user(test_client: AsyncClient, auth_headers):
    response = await test_client.get("/notifications/", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_notification_created_when_appointment_is_scheduled(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """notify_node runs against the patient's linked user ("jamie_login",
    linked inside _submit_referral) once the workflow reaches `scheduled` —
    not the pcp/coordinator account driving the API calls in this test."""
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )

    patient_login = await test_client.post(
        "/auth/login", data={"username": "jamie_login", "password": "testpassword123"},
    )
    patient_headers = {"Authorization": f"Bearer {patient_login.json()['access_token']}"}

    response = await test_client.get("/notifications/", headers=patient_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["referral_id"] == created["id"]
    assert items[0]["read_at"] is None
    assert "scheduled" in items[0]["title"].lower()

    # the coordinator account driving the workflow never gets this notification
    coordinator_notifications = (await test_client.get("/notifications/", headers=auth_headers)).json()
    assert coordinator_notifications["items"] == []


async def test_notification_created_when_outcome_is_recorded_for_referring_doctor(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """test_user_data is both the pcp who created the referral (linked as the
    referring doctor inside _submit_referral) and the account recording the
    outcome here — record_referral_outcome notifies the referring doctor's
    linked user, so this same account should see the notification."""
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )
    outcome_response = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers, json={"symptoms": "back pain"},
    )
    assert outcome_response.status_code == 202

    response = await test_client.get("/notifications/", headers=auth_headers)
    assert response.status_code == 200
    titles = [n["title"] for n in response.json()["items"]]
    assert any("outcome" in t.lower() for t in titles)


async def test_notification_created_for_every_coordinator_when_awaiting_specialist_approval(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """specialist_node's write of awaiting_specialist_approval (the one with
    real candidates attached) broadcasts to every care_coordinator-role
    account — there's no per-referral "assigned coordinator" to target."""
    # Registered and granted care_coordinator *before* the referral is
    # submitted — specialist_node's role-broadcast queries who holds the
    # role at the moment it runs (synchronously, inside this same request
    # under ASGITransport), so a coordinator created afterward wouldn't
    # exist yet to be notified.
    await test_client.post(
        "/auth/register",
        json={"email": "coord1@example.com", "username": "coord1", "password": "testpassword123"},
    )
    await _grant_role(test_session, "coord1", "care_coordinator")
    coord_login = await test_client.post(
        "/auth/login", data={"username": "coord1", "password": "testpassword123"},
    )
    coord_headers = {"Authorization": f"Bearer {coord_login.json()['access_token']}"}

    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123", reason="Persistent lower back pain",
    )

    response = await test_client.get("/notifications/", headers=coord_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["referral_id"] == created["id"]
    assert "approval" in items[0]["title"].lower()

    # The pcp/referring-doctor account driving the workflow isn't itself a
    # coordinator and must not receive this broadcast.
    referring_doctor_notifications = (await test_client.get("/notifications/", headers=auth_headers)).json()
    assert all("approval" not in n["title"].lower() for n in referring_doctor_notifications["items"])


async def test_notification_created_for_referring_doctor_when_eligibility_denied(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """eligibility_node notifies the referral's referring doctor (linked to
    test_user_data's own account inside _submit_referral) when insurance
    can't be verified — an unrecognized policy number keeps this deterministic."""
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="NOT-A-REAL-PLAN", reason="Persistent lower back pain",
    )
    fetched = await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)
    assert fetched.json()["status"] == "eligibility_denied"

    response = await test_client.get("/notifications/", headers=auth_headers)
    assert response.status_code == 200
    titles = [n["title"].lower() for n in response.json()["items"]]
    assert any("eligibility" in t for t in titles)


async def test_mark_notification_read_is_self_scoped(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )
    patient_login = await test_client.post(
        "/auth/login", data={"username": "jamie_login", "password": "testpassword123"},
    )
    patient_headers = {"Authorization": f"Bearer {patient_login.json()['access_token']}"}
    notification = (await test_client.get("/notifications/", headers=patient_headers)).json()["items"][0]

    # the coordinator/pcp account cannot mark the patient's own notification read
    other_attempt = await test_client.post(
        f"/notifications/{notification['id']}/read", headers=auth_headers,
    )
    assert other_attempt.status_code == 404

    own_attempt = await test_client.post(
        f"/notifications/{notification['id']}/read", headers=patient_headers,
    )
    assert own_attempt.status_code == 200
    assert own_attempt.json()["read_at"] is not None

    unread_only = (
        await test_client.get("/notifications/?unread_only=true", headers=patient_headers)
    ).json()
    assert unread_only["items"] == []
