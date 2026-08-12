"""
Document processing service.

Document Chunking milestone: the single orchestration point that
composes document_text_service (parse + upsert DocumentText) and
chunk_service (replace DocumentChunk rows) inside ONE transaction,
so a document's extracted text and its chunks always become durable
together or not at all.

This exists specifically to fix a real consistency gap: if text
persistence and chunk persistence each committed independently (as
two separate service calls each committing on their own), a chunk
persistence failure after a successful text commit would leave a
document with NEW DocumentText paired with OLD (or missing) chunks —
an inconsistent, silently wrong state. Composing both writes here,
flushing but not committing until both succeed, avoids that: this
module's process_document() is the only place a commit happens for
either resource during processing.

Mirrors the same escalating composition pattern already established
in this codebase: document_service composes storage_service;
document_text_service composes storage_service + parse_service; this
module composes document_text_service + chunk_service, one level up.

This is the sole caller of the private, non-committing
document_text_service._upsert_document_text() and
chunk_service._replace_chunks() — the app/api/documents.py process
route calls this module, not those private functions directly, and
not the standalone parse_and_store_document_text() (which still
exists, unchanged, for any caller that wants text alone).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_text import DocumentText
from app.services import chunk_service, document_text_service


async def process_document(
    db: AsyncSession, *, document: Document
) -> tuple[DocumentText, list[DocumentChunk]]:
    """
    Parse `document`, upsert its DocumentText, and replace its
    DocumentChunk rows — all inside one transaction (a single
    db.commit() at the end).

    Failure behavior: if parsing (storage_service.get_file_path() or
    parse_service.extract_text(), called inside
    _upsert_document_text()) raises, nothing has been flushed yet and
    nothing changes. If something fails after the DocumentText upsert
    has been flushed but before this function's own commit (e.g. an
    unexpected error during chunk replacement), no commit has
    happened — FastAPI's get_db dependency exits its `async with
    AsyncSessionLocal()` block on the propagating exception, and
    AsyncSession.close() implicitly rolls back any uncommitted work.
    The previously committed DocumentText and DocumentChunk rows (if
    any, from an earlier successful /process call) are therefore left
    exactly as they were — a document never ends up with new text
    paired with stale or missing chunks.
    """
    document_text = await document_text_service._upsert_document_text(db, document=document)
    chunks = await chunk_service._replace_chunks(db, document_text=document_text)

    await db.commit()
    await db.refresh(document_text)
    for chunk in chunks:
        await db.refresh(chunk)

    return document_text, chunks
