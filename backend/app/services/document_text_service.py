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

Not wired into upload or any endpoint in this checkpoint.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_text import DocumentText
from app.services import parse_service, storage_service


async def parse_and_store_document_text(
    db: AsyncSession, *, document: Document
) -> DocumentText:
    """
    Parse `document`'s stored file and persist the result as a
    DocumentText row, upserting if one already exists.

    Parsing happens strictly before any DocumentText read or write —
    if storage_service.get_file_path() or parse_service.extract_text()
    raises (StoredFileNotFoundError, UnsupportedFormatError, or
    ParseError), it propagates unmodified and this table is never
    touched. This is a consequence of the call ordering below, not a
    try/except/rollback: there is nothing to roll back, because
    nothing has been written yet when a parse failure occurs.

    An empty string ("") from a valid-but-textless PDF is a normal,
    successful result — persisted exactly like any other extracted
    text, with no special-casing.

    Upsert, not insert-only: DocumentText.document_id is UNIQUE, so a
    second call for the same document must not attempt a duplicate
    insert. If a row already exists, its `content` is updated in
    place (created_at stays as the first-parsed time; updated_at
    refreshes to the reparse time, via the existing TimestampMixin —
    no new column needed for that signal). Deliberately not
    versioned: exactly one current DocumentText per Document, per the
    approved Checkpoint 2/3 design.

    Commits internally, matching every existing mutating function in
    document_service.py — the caller does not need to, and should
    not, commit separately.
    """
    file_path = storage_service.get_file_path(document.storage_path)
    content = parse_service.extract_text(file_path)

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

    await db.commit()
    await db.refresh(document_text)
    return document_text
