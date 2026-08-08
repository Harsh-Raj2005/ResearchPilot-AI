"""
Documents router.

Task 3B Checkpoint 3 scope: upload. Document Management CRUD
Checkpoint 1 scope: authenticated listing. Detail/download/delete are
still later checkpoints (see PROJECT_CONTEXT.md Section 13). This is
`get_current_user`'s consumer for both routes — every route here
requires a valid bearer token.

Deliberately thin: routes validate their own query params (pagination
bounds), enforce the size cap on upload (the one piece of validation
that genuinely belongs at this layer — see Section 11 #24), and
delegate everything else to document_service. No business logic and
no direct DB query lives here — ownership filtering happens in the
service, not the router.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
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


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    skip: int = Query(0, ge=0, description="Number of documents to skip"),
    limit: int = Query(20, ge=1, le=100, description="Maximum documents to return"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    """
    List the authenticated user's documents, newest first.

    Bounded pagination via skip/limit query params — plain, no
    pagination framework, matching how the rest of this project favors
    the simplest option that works. limit is capped at 100 so a caller
    can't request an unreasonably large result set in one call.
    """
    documents = await document_service.list_documents_for_user(
        db, user_id=current_user.id, skip=skip, limit=limit
    )
    return [DocumentResponse.model_validate(document) for document in documents]
