"""
Document service.

Task 3B Checkpoint 3 scope: the first place storage_service (Checkpoint
2) and the Document model (Checkpoint 1) are composed together.
Routers call this; it never touches HTTP directly (no HTTPException
here — see app/api/documents.py for how storage_service's domain
exceptions get translated to status codes).

Five functions: create_document (Task 3B Checkpoint 3),
list_documents_for_user (Document Management CRUD Checkpoint 1 —
listing), get_document_for_user (Checkpoint 2 — detail),
get_document_file_for_user (Checkpoint 3 — download), and
delete_document_for_user (Checkpoint 4 — delete, this checkpoint).
This completes the full CRUD surface planned for Document Management.

Deployment milestone: storage_service's three mutating/reading
functions (save_file, get_file_bytes, delete_file) are now async
(Cloudflare R2 via aioboto3) — every call site below is awaited.
get_document_file_for_user() also switched from
storage_service.get_file_path() (which downloads to a temp file, for
parse_service's Path-based contract) to storage_service.get_file_bytes()
(which returns raw bytes directly) — the download endpoint builds an
HTTP response body from bytes, not a local file path, so
FastAPI's FileResponse no longer applies (see app/api/documents.py's
download_document()).
"""
import uuid

from sqlalchemy import select
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
    Save the uploaded file to object storage via storage_service, then
    insert the corresponding Document row.

    Raises storage_service.UnsupportedFileTypeError or StorageError on
    failure — propagated as-is; the router translates these to HTTP
    responses, matching the project's existing service/router
    exception-translation pattern (see auth_service.py).
    """
    saved_file = await storage_service.save_file(
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


async def list_documents_for_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 20,
) -> list[Document]:
    """
    Return this user's documents, newest first, bounded by skip/limit.

    Filters by user_id at the query level — this is the mandatory
    ownership-isolation requirement, not something the router or
    caller can accidentally omit. Pagination bounds (skip >= 0,
    1 <= limit <= 100) are validated at the API boundary (see
    app/api/documents.py's Query() constraints); this function trusts
    its caller and applies whatever skip/limit it's given directly.
    """
    result = await db.execute(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_document_for_user(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Document | None:
    """
    Return a single document if it exists AND belongs to user_id,
    otherwise None.

    Both conditions are in the same WHERE clause — deliberately not
    "fetch by id, then check ownership in Python" — so there is no
    code path that ever loads another user's row into memory, and a
    wrong-owner request is structurally indistinguishable (at the
    query level, and therefore at the response level) from a
    nonexistent id. The router turns None into a single, uniform 404
    for both cases — never revealing which one occurred.
    """
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_document_file_for_user(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[Document, bytes] | None:
    """
    Return the ownership-verified Document together with its actual
    stored file's raw bytes, or None if no matching document exists
    for this user.

    Reuses get_document_for_user() for the existence+ownership check
    rather than duplicating that WHERE clause — same indistinguishable-
    404 guarantee as the detail endpoint. Composes storage_service
    (matching create_document's existing pattern of this service being
    where storage_service and Document meet).

    Raises storage_service.StoredFileNotFoundError if the Document row
    is real and owned by this user but its storage_path no longer
    points to a real object in storage — the router translates this to
    a server error distinct from the "document not found" 404, per
    the security requirement that these two situations not be conflated.
    """
    document = await get_document_for_user(db, document_id=document_id, user_id=user_id)
    if document is None:
        return None
    file_bytes = await storage_service.get_file_bytes(document.storage_path)
    return document, file_bytes


async def delete_document_for_user(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """
    Delete the stored object and the Document row for a document owned
    by user_id.

    Returns True if a matching document was found and deleted, False
    if none exists for this user — the router turns False into the
    same 404 used by detail/download, so a wrong-owner request and a
    nonexistent-id request remain indistinguishable here too. Reuses
    get_document_for_user() rather than a second ownership query.

    Deletion order — the central data-integrity decision for this
    checkpoint, since a Postgres row and an R2 object can't be removed
    in one atomic transaction:

    1. The object is deleted FIRST, via storage_service.delete_file(),
       which is already idempotent (a missing object is treated as
       success, not an error — see storage_service.py).
    2. Only once that succeeds (or was already a no-op) is the
       Document row deleted and the transaction committed.

    Why this order, not the reverse: if step 1 succeeds but step 2
    fails before commit, the result is a DB row referencing a
    now-missing object. That is not a new failure mode — it's exactly
    what get_document_file_for_user() (Checkpoint 3) already handles
    cleanly (a distinct 500 via StoredFileNotFoundError, not a crash).
    The row stays visible and deletable, and retrying DELETE succeeds,
    since delete_file() is a no-op the second time. If the order were
    reversed — row deleted and committed first, object deletion attempted
    second — a failure in that second step orphans the object in R2
    with no DB row ever able to reference it again: a silent,
    permanent storage leak with no retry path. File-first is strictly
    safer given delete_file()'s existing idempotency guarantee.

    If delete_file() raises StorageError (a genuine storage-provider
    failure, distinct from "already missing"), that exception
    propagates and the Document row is deliberately left untouched —
    "row still there, object still there" is a safe, inspectable
    state; "row gone, object orphaned" is not.
    """
    document = await get_document_for_user(db, document_id=document_id, user_id=user_id)
    if document is None:
        return False

    await storage_service.delete_file(document.storage_path)

    await db.delete(document)
    await db.commit()
    return True
