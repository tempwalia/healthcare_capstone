import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import List, Set

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.llm import StubChatModel, get_chat_model
from app.agents.nodes.specialist import _SPECIALTY_KEYWORDS
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

# POC convenience: a referral's own free-text `reason` is available the
# instant this node runs (it's submitted with the referral itself, unlike
# uploaded documents — see specialist.py's infer_specialty docstring for the
# same observation), so it's treated as an equally valid substitute for
# uploaded documents rather than a hard blocker on both document *types*
# being present. If neither reason nor any document exists yet, one of these
# sample referral-letter/report pairs is auto-attached (clearly labeled) so
# the workflow always has something to extract from and never dead-ends
# waiting on a real upload that may never come in a demo/POC setting.
SAMPLE_DOCUMENTS_DIR = Path(__file__).resolve().parents[3] / "sample_documents"
_SAMPLE_DOCUMENT_PAIRS_BY_SPECIALTY = {
    "Orthopedics": ("orthopedics_referral_letter.txt", "orthopedics_mri_imaging_report.txt"),
    "Cardiology": ("cardiology_referral_letter.txt", "cardiology_lab_results.txt"),
    "Dermatology": ("dermatology_referral_letter.txt", "dermatology_skin_scan_report.txt"),
}
_SAMPLE_DOCUMENT_PAIRS = list(_SAMPLE_DOCUMENT_PAIRS_BY_SPECIALTY.values())


def _pick_sample_document_pair(reason: str) -> tuple:
    """Prefer the pair matching the reason's specialty keyword (so the
    auto-attached codes don't contradict what the referral is actually
    about, and specialist recommendation/analytics stay coherent); fall back
    to any of the 3 at random when the reason is blank or unrecognized —
    "randomly assigned" per the original ask, just not at the cost of
    picking a pair that fights the reason text when one is given."""
    text = (reason or "").lower()
    for specialty, keywords in _SPECIALTY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return _SAMPLE_DOCUMENT_PAIRS_BY_SPECIALTY[specialty]
    return random.choice(_SAMPLE_DOCUMENT_PAIRS)


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


async def _auto_attach_sample_documents(db, referral: ReferralRequest) -> List[ReferralDocument]:
    """POC convenience: a referral with no real upload shouldn't dead-end at
    `awaiting_documents` waiting on a file that may never come. Picks a
    sample referral-letter/report pair matching the reason's specialty when
    recognizable, otherwise a random one (filename prefixed so it's
    obviously an auto-attached placeholder in the UI, not a real upload) so
    extraction always has real text — and a real ICD-10/CPT signal for
    specialty routing — to work with."""
    letter_name, report_name = _pick_sample_document_pair(referral.reason)
    attached: List[ReferralDocument] = []
    for name in (letter_name, report_name):
        src = SAMPLE_DOCUMENTS_DIR / name
        if not src.exists():
            continue
        doc = ReferralDocument(
            referral_request_id=referral.id,
            filename=f"[auto-sample] {name}",
            storage_path=str(src),
            extraction_status="queued",
        )
        db.add(doc)
        attached.append(doc)
    if attached:
        await db.flush()
    return attached


async def intake_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, state["referral_id"])
        docs = (
            await db.execute(
                select(ReferralDocument).where(ReferralDocument.referral_request_id == state["referral_id"])
            )
        ).scalars().all()

        has_reason = bool((referral.reason or "").strip())
        if not docs:
            docs = await _auto_attach_sample_documents(db, referral)

        # pypdf's page-by-page parsing is CPU-bound and was previously run
        # inline on the event loop, one document at a time — for a real PDF
        # that stalls every other concurrent request/background task. Now
        # off-loaded to a thread and run concurrently across documents.
        texts = await asyncio.gather(*(asyncio.to_thread(extract_document_text, d.storage_path) for d in docs))
        text = "\n".join(t for t in texts if t)

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
        missing_types = sorted(REQUIRED_DOC_TYPES - present_types)
        # A referral only *blocks* on documents if it has neither any
        # document nor its own reason text — one uploaded document (of
        # either type) or a filled-in reason is enough to move forward.
        # `missing_types` below is now purely an informational "these types
        # would help" hint on the response, not a gate (see route_after_intake).
        can_proceed = bool(docs) or has_reason

        referral.status = (
            ReferralWorkflowStatus.ELIGIBILITY_CHECKING.value if can_proceed
            else ReferralWorkflowStatus.AWAITING_DOCUMENTS.value
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
            "missing_documents": [] if can_proceed else missing_types,
            "status": referral.status,
        }
