"""
Document service.

Task 3B Checkpoint 3 scope: the first place storage_service (Checkpoint
2) and the Document model (Checkpoint 1) are composed together.
Routers call this; it never touches HTTP directly (no HTTPException
here — see app/api/documents.py for how storage_service's domain
exceptions get translated to status codes).

Only one function so far (create_document) — list/get/delete are
explicitly out of scope for this checkpoint and will be added here
when their own checkpoints arrive, following the same pattern as
auth_service.py.
"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.services import storage_service


async def create_document(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    content: bytes,
    original_filename: str,
    content_type: str,
) -> Document:
    """
    Save the uploaded file to disk via storage_service, then insert
    the corresponding Document row.

    Raises storage_service.UnsupportedFileTypeError or StorageError on
    failure — propagated as-is; the router translates these to HTTP
    responses, matching the project's existing service/router
    exception-translation pattern (see auth_service.py).
    """
    saved_file = storage_service.save_file(
        content=content, original_filename=original_filename, content_type=content_type
    )

    document = Document(
        user_id=user_id,
        original_filename=saved_file.original_filename,
        stored_filename=saved_file.stored_filename,
        content_type=saved_file.content_type,
        file_size_bytes=saved_file.file_size_bytes,
        storage_path=saved_file.storage_path,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document
