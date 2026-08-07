"""Mock Provider Directory / Scheduling-adjacent directory system.

Contract: GET /providers/search?specialty=&location=&insurance_plan_id= ->
ranked-candidate-shaped list (distance/rating/network/next availability).
Exposed to agents as the MCP tool `search_providers` — feeds the specialist
recommendation step (AI Opportunity #2).

`insurance_plan_id` is matched against each provider's own small
`in_network_plan_ids` list, standing in for "this specialist accepts this
payer's plan" — a real directory would look this up against payer-published
network data, not our internal Postgres `insurance_plans.id`, but reusing that
same integer id keeps the mock demoable without a second ID-mapping layer.
"""
from typing import List, Optional

from fastapi import FastAPI
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel

app = FastAPI(
    title="Mock Provider Directory System",
    description="Stand-in for an external specialist/provider directory.",
)

PROVIDERS = [
    {"doctor_id": 88, "name": "Dr. Priya Rao", "specialty": "Orthopedics", "location": "Downtown", "distance_mi": 0.9, "rating": 4.8, "next_available_days": 3, "in_network_plan_ids": [1, 2]},
    {"doctor_id": 91, "name": "Dr. Daniel Kim", "specialty": "Orthopedics", "location": "Downtown", "distance_mi": 2.1, "rating": 4.6, "next_available_days": 5, "in_network_plan_ids": [1]},
    {"doctor_id": 12, "name": "Dr. Maria Chen", "specialty": "Cardiology", "location": "Midtown", "distance_mi": 3.4, "rating": 4.9, "next_available_days": 7, "in_network_plan_ids": [1, 2]},
    {"doctor_id": 45, "name": "Dr. James Okoye", "specialty": "Cardiology", "location": "Downtown", "distance_mi": 1.2, "rating": 4.3, "next_available_days": 2, "in_network_plan_ids": [2]},
    {"doctor_id": 67, "name": "Dr. Lena Novak", "specialty": "Dermatology", "location": "Uptown", "distance_mi": 5.0, "rating": 4.5, "next_available_days": 10, "in_network_plan_ids": [1]},
    {"doctor_id": 73, "name": "Dr. Omar Farouk", "specialty": "Orthopedics", "location": "Uptown", "distance_mi": 4.2, "rating": 4.1, "next_available_days": 1, "in_network_plan_ids": []},
]


class ProviderCandidate(BaseModel):
    doctor_id: int
    name: str
    specialty: str
    location: str
    distance_mi: float
    rating: float
    next_available_days: int
    in_network: bool


def _build_candidate(provider: dict, insurance_plan_id: Optional[int]) -> ProviderCandidate:
    return ProviderCandidate(
        doctor_id=provider["doctor_id"],
        name=provider["name"],
        specialty=provider["specialty"],
        location=provider["location"],
        distance_mi=provider["distance_mi"],
        rating=provider["rating"],
        next_available_days=provider["next_available_days"],
        in_network=insurance_plan_id is not None and insurance_plan_id in provider["in_network_plan_ids"],
    )


@app.get("/providers/search", response_model=List[ProviderCandidate], operation_id="search_providers")
async def search_providers(
    specialty: str,
    location: Optional[str] = None,
    insurance_plan_id: Optional[int] = None,
):
    specialty_matches = [p for p in PROVIDERS if specialty.strip().lower() in p["specialty"].lower()]

    if location:
        location_matches = [p for p in specialty_matches if location.strip().lower() in p["location"].lower()]
        # This toy directory only knows a handful of canned locations
        # ("Downtown"/"Midtown"/"Uptown") — a real caller-entered
        # location/city that doesn't match any of them shouldn't zero out
        # every candidate and leave a referral permanently stuck at
        # awaiting_specialist_approval with nothing to review. Location
        # narrows results when it actually matches something; it never
        # eliminates every option when it matches nothing at all.
        candidates = location_matches or specialty_matches
    else:
        candidates = specialty_matches

    results = [_build_candidate(p, insurance_plan_id) for p in candidates]
    return sorted(results, key=lambda p: p.distance_mi)


mcp = FastApiMCP(app)
mcp.mount_http()
