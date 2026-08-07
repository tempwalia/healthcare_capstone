"""Mock Payer / Eligibility system.

Contract: POST /eligibility/check {insurance_policy_number, procedure_code} ->
{verified, network_status, copay_estimate_usd, prior_auth_required}. Exposed
to agents as the MCP tool `check_eligibility` — the mandatory "eligibility
verification" step of the referral workflow.
"""
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel

app = FastAPI(title="Mock Payer / Eligibility System", description="Stand-in for an external payer's eligibility API.")

PLANS = {
    "ACME-991123": {"plan": "Acme PPO Gold", "network_doctor_ids": [88, 91, 12], "copay_usd": 40},
    "ACME-778890": {"plan": "Acme HMO Silver", "network_doctor_ids": [12, 45], "copay_usd": 25},
    "HORIZON-556677": {"plan": "Horizon Blue PPO", "network_doctor_ids": [67, 73], "copay_usd": 30},
    "UNITEDCARE-334455": {"plan": "UnitedCare Basic HMO", "network_doctor_ids": [45, 12], "copay_usd": 55},
}

# Procedure code prefixes that require prior authorization under these plans —
# a toy rule (spine/musculoskeletal ICD-10 codes), not real payer policy.
PRIOR_AUTH_PREFIXES = ("M51", "M54")


class EligibilityRequest(BaseModel):
    insurance_policy_number: str
    procedure_code: str


class EligibilityResponse(BaseModel):
    verified: bool
    network_status: str
    copay_estimate_usd: int | None = None
    prior_auth_required: bool = False


@app.post("/eligibility/check", response_model=EligibilityResponse, operation_id="check_eligibility")
async def check_eligibility(body: EligibilityRequest):
    plan = PLANS.get(body.insurance_policy_number)
    if not plan:
        return EligibilityResponse(verified=False, network_status="unknown")

    prior_auth = body.procedure_code.upper().startswith(PRIOR_AUTH_PREFIXES)
    return EligibilityResponse(
        verified=True,
        network_status="in_network",
        copay_estimate_usd=plan["copay_usd"],
        prior_auth_required=prior_auth,
    )


mcp = FastApiMCP(app)
mcp.mount_http()
