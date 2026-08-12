"""
Documents router.

Task 3B Checkpoint 3 scope: upload. Document Management CRUD
Checkpoint 1: authenticated listing. Checkpoint 2: authenticated
detail. Checkpoint 3: authenticated download. Checkpoint 4: authenticated
delete — completed the full CRUD surface. Document Text Extraction
Checkpoint 5 (this checkpoint): authenticated processing — triggers
text extraction for an already-uploaded document on demand. This is
`get_current_user`'s consumer for all six routes — every route here
requires a valid bearer token.

Deliberately thin: routes validate their own path/query params
(document_id's type, pagination bounds), enforce the size cap on
upload (the one piece of validation that genuinely belongs at this
layer — see Section 11 #24), and delegate everything else to
document_service / document_text_service. No business logic and no
direct DB query or filesystem access lives here.

Upload does NOT call document_text_service — processing is an
explicit, separate operation the caller triggers via
POST /{document_id}/process, not an automatic side effect of upload.
See PROJECT_CONTEXT.md for the design rationale (Checkpoint 5).
"""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.document import DocumentResponse
from app.services import document_processing_service, document_service, parse_service, storage_service

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


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """
    Return metadata for a single document owned by the authenticated
    user.

    A nonexistent document and another user's document return the
    identical 404 — the service's combined WHERE clause (id AND
    user_id) means there's no code path where this router ever learns
    which case it was, so there's nothing to accidentally leak.
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    return DocumentResponse.model_validate(document)


@router.get("/{document_id}/file")
async def download_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """
    Return the actual stored file for a document owned by the
    authenticated user.

    Same indistinguishable-404 behavior as the detail endpoint for
    "doesn't exist" vs. "belongs to someone else" — both flow through
    the same get_document_for_user() ownership check inside
    document_service.get_document_file_for_user(). A document that
    exists and is owned by the caller, but whose file is missing from
    disk, is a different situation (a server-side data integrity
    problem, not a client error) and gets a distinct 500 — without
    ever revealing the filesystem path in the response.

    No response_model here: the body is the file itself, not a JSON
    DocumentResponse — the two are mutually exclusive for this route.
    """
    try:
        result = await document_service.get_document_file_for_user(
            db, document_id=document_id, user_id=current_user.id
        )
    except storage_service.StoredFileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored file for this document could not be found.",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    document, file_path = result
    return FileResponse(
        path=file_path,
        media_type=document.content_type,
        filename=document.original_filename,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a document (stored file + database row) owned by the
    authenticated user.

    204 No Content on success — no existing project convention uses a
    custom JSON success body for an action endpoint, and 204 is the
    conventional REST response for a delete with nothing further to
    return. Same indistinguishable-404 behavior as detail/download for
    "doesn't exist" vs. "belongs to someone else" — both flow through
    the same get_document_for_user() check inside
    document_service.delete_document_for_user(). A genuine filesystem
    failure while deleting the file (StorageError — distinct from
    "already missing", which delete_file() treats as success) is a
    500, and deliberately leaves the Document row untouched rather
    than deleting it — see document_service.delete_document_for_user's
    docstring for the full data-integrity reasoning.

    Note: a second DELETE of the same document_id returns 404, not
    another 204 — once deleted, the document genuinely no longer
    exists for this user, so "not found" is the accurate response,
    not a false "deleted again" success.
    """
    try:
        deleted = await document_service.delete_document_for_user(
            db, document_id=document_id, user_id=current_user.id
        )
    except storage_service.StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete the stored file for this document.",
        ) from exc

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )


@router.post("/{document_id}/process", response_model=DocumentResponse)
async def process_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """
    Parse a document owned by the authenticated user and persist the
    extracted text — Document Text Extraction Checkpoint 5's explicit
    processing operation.

    Deliberately a separate, on-demand endpoint rather than an
    automatic step of upload (see PROJECT_CONTEXT.md for the full
    Checkpoint 5 design rationale): upload's existing behavior is
    completely unchanged by this route's addition.

    Same indistinguishable-404 behavior as every other {document_id}
    route for "doesn't exist" vs. "belongs to someone else" — both
    flow through the existing get_document_for_user() ownership
    check, reused as-is rather than duplicated here.

    Returns the existing DocumentResponse (200) on success — the same
    Document, re-serialized. Deliberately never returns DocumentText
    or its `content`: extracted text remains internal processing
    state, not something this (or any) endpoint exposes (see
    PROJECT_CONTEXT.md Section 11 #54).

    Calling this endpoint again for an already-processed document is
    how reprocessing works — DocumentText and DocumentChunk rows are
    both replaced (via document_processing_service.process_document(),
    which upserts DocumentText and deletes+recreates DocumentChunk
    rows) inside one transaction, so no separate reprocess mechanism
    or duplicate-prevention logic is needed here, and a document never
    ends up with new text paired with stale chunks.

    Error mapping, mirroring this file's existing exception-translation
    style (see upload/download above):
    - UnsupportedFormatError -> 422 (the document's content can't be
      processed by this parser, e.g. a non-PDF upload)
    - ParseError -> 422 (the file exists but its content is invalid
      or corrupted, so extraction can't produce a result)
    - StoredFileNotFoundError -> 500 (a server-side data-integrity
      problem — the Document row is valid and owned by the caller,
      but its file is missing from disk — distinct from a client
      error, same as download's identical handling)
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    try:
        await document_processing_service.process_document(db, document=document)
    except (parse_service.UnsupportedFormatError, parse_service.ParseError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except storage_service.StoredFileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored file for this document could not be found.",
        ) from exc

    return DocumentResponse.model_validate(document)
