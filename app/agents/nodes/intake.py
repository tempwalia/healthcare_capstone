import json
import logging
import re
from pathlib import Path
from typing import List, Set

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.llm import StubChatModel, get_chat_model
from app.agents.state import ReferralState
from app.database import session as db_session
from app.events.outbox import write_outbox_event
from app.models.referral import ReferralDocument, ReferralRequest, ReferralWorkflowStatus

logger = logging.getLogger(__name__)

REQUIRED_DOC_TYPES = {"referral_letter", "recent_imaging_or_labs"}

_DOC_TYPE_KEYWORDS = {
    "referral_letter": ("referral", "letter"),
    "recent_imaging_or_labs": (
        "mri", "x-ray", "xray", "imaging", "lab", "labs", "scan", "ultrasound", "radiology", "ct ",
    ),
}

# ICD-10: a letter (excluding U), two digits, optionally a decimal point and
# up to 4 more alphanumeric characters (e.g. M51.26). CPT: plain 5-digit
# codes — broad by design, this is the deterministic fallback path, not the
# precision-critical one (that's the LLM path below).
_ICD10_PATTERN = re.compile(r"\b([A-TV-Z]\d{2}(?:\.[0-9A-Z]{1,4})?)\b", re.IGNORECASE)
_CPT_PATTERN = re.compile(r"\b(\d{5})\b")


class ExtractedCodes(BaseModel):
    diagnosis_codes: List[str] = Field(description="ICD-10 codes found or inferred from the text")
    procedure_codes: List[str] = Field(description="CPT codes found or inferred from the text")
    confidence: float = Field(ge=0, le=1)


INTAKE_EXTRACTION_PROMPT = (
    "Extract every ICD-10 diagnosis code and CPT procedure code mentioned or clearly implied in this "
    "referral document text. If a code isn't explicitly written but the diagnosis is described in "
    "plain language, infer the most specific matching ICD-10 code. Return an empty list for a "
    "category with nothing found, and a confidence between 0 and 1.\n\nDocument text:\n{document_text}"
)


def extract_document_text(storage_path: str) -> str:
    """`.pdf` via pypdf; `.txt` read directly (this project's own upload
    tests use plain text referral letters, not just PDFs — treating both as
    valid document text is strictly more capable than the PDF-only guide
    sketch, not a narrowing of it). Any other extension, or a file that fails
    to parse, contributes no text rather than failing the whole node —
    matches ADR-005's "never a 500" resilience posture."""
    path = Path(storage_path)
    try:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if suffix == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        logger.warning("intake: failed to extract text from %s", storage_path, exc_info=True)
    return ""


def infer_document_types(docs: List[ReferralDocument]) -> Set[str]:
    present: Set[str] = set()
    for doc in docs:
        name = (doc.filename or "").lower()
        for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
            if any(keyword in name for keyword in keywords):
                present.add(doc_type)
    return present


def regex_extract_icd10(text: str) -> List[str]:
    return sorted({code.upper() for code in _ICD10_PATTERN.findall(text)})


def regex_extract_cpt(text: str) -> List[str]:
    return sorted(set(_CPT_PATTERN.findall(text)))


async def intake_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        docs = (
            await db.execute(
                select(ReferralDocument).where(ReferralDocument.referral_request_id == state["referral_id"])
            )
        ).scalars().all()
        text = "\n".join(t for t in (extract_document_text(d.storage_path) for d in docs) if t)

        llm = get_chat_model("code_extraction")
        if isinstance(llm, StubChatModel):
            diagnosis_codes = regex_extract_icd10(text)
            procedure_codes = regex_extract_cpt(text)
        else:
            try:
                extracted = await llm.with_structured_output(ExtractedCodes).ainvoke(
                    INTAKE_EXTRACTION_PROMPT.format(document_text=text[:8000])
                )
                diagnosis_codes, procedure_codes = extracted.diagnosis_codes, extracted.procedure_codes
            except Exception:
                logger.warning("intake: LLM code extraction failed, falling back to regex", exc_info=True)
                diagnosis_codes = regex_extract_icd10(text)
                procedure_codes = regex_extract_cpt(text)

        present_types = infer_document_types(docs)
        missing = sorted(REQUIRED_DOC_TYPES - present_types)

        referral = await db.get(ReferralRequest, state["referral_id"])
        referral.status = (
            ReferralWorkflowStatus.AWAITING_DOCUMENTS.value if missing
            else ReferralWorkflowStatus.ELIGIBILITY_CHECKING.value
        )
        for d in docs:
            d.extraction_status = "complete"
            d.extracted_diagnosis_codes = json.dumps(diagnosis_codes)
            d.extracted_procedure_codes = json.dumps(procedure_codes)
        await write_outbox_event(
            db, "referral.status.changed",
            {"referral_id": referral.id, "to_status": referral.status},
            referral_id=referral.id,
        )
        await db.commit()

        return {
            "diagnosis_codes": diagnosis_codes,
            "procedure_codes": procedure_codes,
            "missing_documents": missing,
            "status": referral.status,
        }
