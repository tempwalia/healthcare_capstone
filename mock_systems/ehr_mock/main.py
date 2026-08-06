"""Mock EHR (Electronic Health Record) system.

Stands in for a real hospital/clinic EHR the platform doesn't own. Deliberately
has its own independent, in-memory dataset — a real integration would call out
to a partner system over FHIR/HL7, not read our own Postgres directly, and the
mock preserves that boundary so the integration contract stays honest.

Contract: GET /patients/{patient_id}/history -> prior diagnoses, medications,
allergies. Exposed to agents as the MCP tool `get_patient_history`.
"""
from typing import List

from fastapi import FastAPI
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel

app = FastAPI(title="Mock EHR System", description="Stand-in for an external hospital/clinic EHR.")

# Seeded so patient_id=1 (the demo patient created during earlier smoke tests)
# has a believable prior history; any other id gracefully returns an empty one.
PATIENT_HISTORY = {
    1: {
        "prior_diagnoses": ["M54.5 - Low back pain", "I10 - Essential hypertension"],
        "medications": ["Lisinopril 10mg daily"],
        "allergies": ["Penicillin"],
    },
}


class PatientHistoryResponse(BaseModel):
    patient_id: int
    prior_diagnoses: List[str]
    medications: List[str]
    allergies: List[str]


@app.get(
    "/patients/{patient_id}/history",
    response_model=PatientHistoryResponse,
    operation_id="get_patient_history",
)
async def get_patient_history(patient_id: int):
    record = PATIENT_HISTORY.get(patient_id, {"prior_diagnoses": [], "medications": [], "allergies": []})
    return {"patient_id": patient_id, **record}


mcp = FastApiMCP(app)
mcp.mount_http()
