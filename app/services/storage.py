import uuid
from pathlib import Path

from fastapi import UploadFile

UPLOAD_ROOT = Path("uploads") / "referral_documents"


async def save_referral_document(referral_id: int, upload: UploadFile) -> str:
    """Local-disk storage for this capstone's scope — swap for S3/object
    storage behind this one function if deploying for real."""
    directory = UPLOAD_ROOT / str(referral_id)
    directory.mkdir(parents=True, exist_ok=True)

    suffix = Path(upload.filename or "upload").suffix
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    destination = directory / stored_name

    content = await upload.read()
    destination.write_bytes(content)

    return str(destination)
