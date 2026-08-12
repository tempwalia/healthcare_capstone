import uuid
from pathlib import Path

from fastapi import UploadFile

UPLOAD_ROOT = Path("uploads") / "referral_documents"
MEDICAL_RECORD_UPLOAD_ROOT = Path("uploads") / "medical_record_documents"


async def _save_upload(root: Path, scope_id: int, upload: UploadFile) -> str:
    directory = root / str(scope_id)
    directory.mkdir(parents=True, exist_ok=True)

    suffix = Path(upload.filename or "upload").suffix
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    destination = directory / stored_name

    content = await upload.read()
    destination.write_bytes(content)

    return str(destination)


async def save_referral_document(referral_id: int, upload: UploadFile) -> str:
    """Local-disk storage for this capstone's scope — swap for S3/object
    storage behind this one function if deploying for real."""
    return await _save_upload(UPLOAD_ROOT, referral_id, upload)


async def save_medical_record_document(record_id: int, upload: UploadFile) -> str:
    """Same local-disk convention as save_referral_document, scoped to a
    medical record instead of a referral."""
    return await _save_upload(MEDICAL_RECORD_UPLOAD_ROOT, record_id, upload)
