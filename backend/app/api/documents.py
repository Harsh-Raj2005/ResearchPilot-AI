"""
Documents router.

Task 3B Checkpoint 3 scope: upload only. List/detail/download/delete
are later checkpoints (see PROJECT_CONTEXT.md Section 13). This is
`get_current_user`'s first real consumer — every route here requires
a valid bearer token.

Deliberately thin: reads the upload, enforces the size cap (the one
piece of validation that genuinely belongs at this layer — see
Section 11 #24), delegates everything else to document_service, and
translates its domain exceptions to HTTP responses. No business logic
lives here.
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.document import DocumentResponse
from app.services import document_service, storage_service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    content = await file.read()

    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.max_upload_size_mb}MB",
        )

    try:
        document = await document_service.create_document(
            db,
            user_id=current_user.id,
            content=content,
            original_filename=file.filename or "",
            content_type=file.content_type or "application/octet-stream",
        )
    except storage_service.UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except storage_service.StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    return DocumentResponse.model_validate(document)
