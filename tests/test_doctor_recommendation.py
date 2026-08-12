"""GET /doctors/recommend — "suggest a doctor for me" for direct
appointment booking (no referral involved). Reuses the exact same
specialty-inference + ranking logic the referral workflow's specialist
recommendation step already uses (app.services.doctor_recommendation),
sourced from our own bookable `doctors` table instead of the external mock
provider directory.
"""
from httpx import AsyncClient

from tests.test_referral import _grant_role


async def test_recommend_doctors_matches_specialty_and_ranks_candidates(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    cardiologist = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    orthopedist_data = {**test_doctor_data, "first_name": "Priya", "email": "dr.priya@hospital.com",
                         "specialization": "Orthopedics", "license_number": "MD999999"}
    orthopedist = (await test_client.post("/doctors/", json=orthopedist_data, headers=auth_headers)).json()

    response = await test_client.get(
        "/doctors/recommend", params={"reason": "chest pain and heart palpitations"}, headers=auth_headers,
    )
    assert response.status_code == 200
    candidates = response.json()
    assert len(candidates) >= 1
    assert all("score" in c and "reasons" in c for c in candidates)
    doctor_ids = {c["doctor_id"] for c in candidates}
    # Specialty inference ("heart"/"palpitation" -> Cardiology) should
    # surface the cardiologist, not the unrelated orthopedist.
    assert cardiologist["id"] in doctor_ids
    assert orthopedist["id"] not in doctor_ids


async def test_recommend_doctors_falls_back_to_all_when_no_specialty_match(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    await _grant_role(test_session, test_user_data["username"], "pcp")
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()

    response = await test_client.get(
        "/doctors/recommend", params={"specialty": "Neurology"}, headers=auth_headers,
    )
    assert response.status_code == 200
    candidates = response.json()
    assert any(c["doctor_id"] == doctor["id"] for c in candidates)


async def test_recommend_doctors_prioritizes_same_city_doctor(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data, test_patient_data,
):
    """Simple case-insensitive city/region text match (no lat/long) —
    app.services.doctor_recommendation.recommend_platform_doctors sets
    distance_mi=0 for a same-city doctor, which rule_based_rank's existing
    scoring formula already knows how to reward."""
    await _grant_role(test_session, test_user_data["username"], "pcp")
    patient = (await test_client.post(
        "/patients/", json={**test_patient_data, "city": "Springfield"}, headers=auth_headers,
    )).json()
    nearby = (await test_client.post(
        "/doctors/", json={**test_doctor_data, "city": "springfield"}, headers=auth_headers,
    )).json()
    far_data = {**test_doctor_data, "first_name": "Remote", "email": "dr.remote@hospital.com",
                "license_number": "MD-FAR-1", "city": "Riverside"}
    far = (await test_client.post("/doctors/", json=far_data, headers=auth_headers)).json()

    response = await test_client.get(
        "/doctors/recommend", params={"specialty": "Cardiology", "patient_id": patient["id"]}, headers=auth_headers,
    )
    assert response.status_code == 200
    candidates = response.json()
    by_id = {c["doctor_id"]: c for c in candidates}
    assert by_id[nearby["id"]]["score"] > by_id[far["id"]]["score"]
    assert candidates[0]["doctor_id"] == nearby["id"]


async def test_doctors_list_supports_name_search(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    """GET /doctors/?q= — the "search by name irrespective of specialty or
    location" fallback for when recommendation doesn't surface who the user
    is looking for."""
    await _grant_role(test_session, test_user_data["username"], "pcp")
    target = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    other_data = {**test_doctor_data, "first_name": "Priya", "last_name": "Rao",
                  "email": "dr.rao@hospital.com", "license_number": "MD-OTHER-9"}
    await test_client.post("/doctors/", json=other_data, headers=auth_headers)

    response = await test_client.get(
        f"/doctors/?q={test_doctor_data['last_name']}", headers=auth_headers,
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert {d["id"] for d in items} == {target["id"]}
