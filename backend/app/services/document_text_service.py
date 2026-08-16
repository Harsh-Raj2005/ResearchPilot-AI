"""
Document text service.

Document Text Extraction Checkpoint 4 scope: the first place
parse_service (Checkpoint 1) and the DocumentText table (Checkpoint 3)
are composed together — the parse -> persist bridge. Mirrors how
document_service composes storage_service: this module composes both
storage_service (to locate the file) and parse_service (to read it),
then persists the result.

Deliberately does NOT perform any authorization — it receives an
already-authorized Document object, not a document_id, so there is no
ownership check to duplicate here. Whoever calls this function is
responsible for having obtained the Document through an authorized
path (document_service.get_document_for_user, or the object
document_service.create_document already returns).

Document Chunking milestone: split into a private, non-committing
core (_upsert_document_text) and the existing public function
(parse_and_store_document_text), which still parses, upserts, AND
commits exactly as it always has — every existing caller and all 8
of this module's existing tests are unaffected. The private core
exists so document_processing_service.process_document() can compose
this module's write with chunk_service's chunk write inside one
shared transaction (single commit for both), rather than each module
committing independently and risking a DocumentText/DocumentChunk
inconsistency if the second write fails. See PROJECT_CONTEXT.md for
the full transaction-consistency rationale.
"""
import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_text import DocumentText
from app.services import parse_service, storage_service


async def _cleanup_temp_file(file_path: Path) -> None:
    """
    Deletes the temporary local file get_file_path() downloaded.

    The real fix for the Windows [WinError 32] failure this project
    hit lives in parse_service.extract_text(), not here: that function
    now hands PyMuPDF a byte stream instead of a path, so PyMuPDF
    never holds an OS-level file handle and nothing can remain locked.
    (The earlier theory — that this was an mmap teardown race that
    retrying would resolve — was wrong. The actual cause was that
    `pymupdf.open()` raises during construction for a corrupt PDF, so
    the surrounding `with` block was never entered and close() never
    ran, leaking the handle indefinitely. No amount of retrying could
    have fixed that.)

    The bounded retry below is therefore no longer load-bearing; it is
    kept only as cheap defence-in-depth against an unrelated transient
    lock (e.g. an antivirus scanner briefly holding a newly written
    file on Windows). It uses asyncio.sleep(), not time.sleep(), since
    this runs inside an async call stack and must not block the event
    loop.

    A persistent failure is swallowed rather than raised: this is only
    ever called from a `finally` block, and cleanup must never replace
    or mask the real exception (or successful result) it is cleaning
    up after. Note that the tests assert the temp file is genuinely
    gone, so a regression in the parse_service fix would still surface
    as a test failure rather than being silently hidden here.
    """
    for attempt in range(3):
        try:
            file_path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 2:
                return
            await asyncio.sleep(0.1)


async def _upsert_document_text(db: AsyncSession, *, document: Document) -> DocumentText:
    """
    Parse `document`'s stored file and stage the result as a
    DocumentText row (insert or update-in-place), flushing but NOT
    committing.

    Parsing happens strictly before any DocumentText read or write —
    if storage_service.get_file_path() or parse_service.extract_text()
    raises (StoredFileNotFoundError, UnsupportedFormatError, or
    ParseError), it propagates unmodified and this table is never
    touched.

    Deployment milestone: get_file_path() now downloads the object
    from R2 to a temporary local file (parse_service.extract_text()
    still needs a real Path — see storage_service.py's own docstring
    for why that function is deliberately unmodified). This function
    is responsible for deleting that temp file once parsing is done,
    in a `finally` block, so a temp file never survives past a single
    call — regardless of whether parsing succeeds or raises. See
    _cleanup_temp_file()'s own docstring for a Windows-specific
    cleanup nuance this milestone's own manual verification surfaced.

    An empty string ("") from a valid-but-textless PDF is a normal,
    successful result — persisted exactly like any other extracted
    text, with no special-casing.

    Upsert, not insert-only: DocumentText.document_id is UNIQUE, so a
    second call for the same document must not attempt a duplicate
    insert. If a row already exists, its `content` is updated in
    place (created_at stays as the first-parsed time; updated_at
    refreshes to the reparse time via the existing TimestampMixin).
    Deliberately not versioned: exactly one current DocumentText per
    Document, per the approved Checkpoint 2/3 design.

    Flushes (so document_text.id is available to a caller that needs
    it, e.g. for a chunk's foreign key, within the same open
    transaction) but deliberately does not commit — the caller
    decides the transaction boundary. See parse_and_store_document_text()
    for the self-committing standalone entry point.
    """
    file_path = await storage_service.get_file_path(document.storage_path)
    try:
        content = parse_service.extract_text(file_path)
    finally:
        await _cleanup_temp_file(file_path)

    result = await db.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.content = content
        document_text = existing
    else:
        document_text = DocumentText(document_id=document.id, content=content)
        db.add(document_text)

    await db.flush()
    return document_text


async def parse_and_store_document_text(
    db: AsyncSession, *, document: Document
) -> DocumentText:
    """
    Parse `document`'s stored file and persist the result as a
    DocumentText row, upserting if one already exists.

    Standalone, self-committing entry point — unchanged behavior from
    before the Document Chunking milestone. Commits internally,
    matching every existing mutating function in document_service.py
    — the caller does not need to, and should not, commit separately.
    Delegates to _upsert_document_text() for the actual parse+stage
    logic; this function is now a thin wrapper adding the commit.
    """
    document_text = await _upsert_document_text(db, document=document)
    await db.commit()
    await db.refresh(document_text)
    return document_text
