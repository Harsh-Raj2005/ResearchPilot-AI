"""
Document processing service.

Document Chunking milestone: the single orchestration point that
composes document_text_service (parse + upsert DocumentText) and
chunk_service (replace DocumentChunk rows) inside ONE transaction,
so a document's extracted text and its chunks always become durable
together or not at all.

Document Chunks -> Embeddings milestone: extends that same
orchestration to also generate and persist each chunk's embedding
before the single commit. The full flow:

    parse
      -> _upsert_document_text()          (flush, no commit)
      -> chunk_service.chunk_text()        (pure, in-memory)
      -> embedding_service.embed_texts()   (external API call —
                                             still before any DB write
                                             for this call is staged)
      -> chunk_service._replace_chunks()   (flush, no commit —
                                             constructs every
                                             DocumentChunk with its
                                             embedding already set)
      -> ONE db.commit()

This ordering is deliberate and load-bearing, not incidental:
DocumentChunk.embedding is NOT NULL, so a chunk row can never be
flushed without an embedding already attached — chunk_text() and
embed_texts() both run, and embed_texts() must succeed, *before*
chunk_service._replace_chunks() constructs a single DocumentChunk
object. This also protects reprocessing: _replace_chunks() only
deletes the previous chunk set once new embeddings are already in
hand, so an embedding failure during reprocessing never destroys the
previously committed chunks — nothing has been deleted yet when the
embedding call could fail.

This exists specifically to fix a real consistency gap (Document
Chunking milestone): if text persistence and chunk persistence each
committed independently, a chunk-persistence failure after a
successful text commit would leave a document with NEW DocumentText
paired with OLD (or missing) chunks — an inconsistent, silently wrong
state. This milestone extends that same guarantee one step further:
a chunk can never exist (committed or even transiently flushed)
without its embedding, and an embedding-provider failure at any point
leaves the previously committed DocumentText/DocumentChunk/embedding
state completely unchanged, relying on the same rollback-on-exception
guarantee documented below.

Mirrors the same escalating composition pattern already established
in this codebase: document_service composes storage_service;
document_text_service composes storage_service + parse_service; this
module composes document_text_service + chunk_service +
embedding_service, one level up.

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
from app.services import chunk_service, document_text_service, embedding_service


async def process_document(
    db: AsyncSession, *, document: Document
) -> tuple[DocumentText, list[DocumentChunk]]:
    """
    Parse `document`, upsert its DocumentText, compute its chunks,
    generate their embeddings, and replace its DocumentChunk rows —
    all inside one transaction (a single db.commit() at the end).

    Failure behavior:
    - If parsing (storage_service.get_file_path() or
      parse_service.extract_text(), called inside
      _upsert_document_text()) raises, nothing has been flushed yet
      and nothing changes.
    - If DocumentText.content is empty, chunk_text() returns an empty
      list — embed_texts() is never called (no embedding request is
      made for zero chunks) and _replace_chunks() is called with
      empty lists, which still correctly deletes any previous chunks
      and inserts none. This preserves the existing, already-tested
      semantics that empty extracted text is a valid, successful
      processing result.
    - If embedding_service.embed_texts() raises EmbeddingProviderError
      (or chunk_service._replace_chunks() raises for any other
      reason), no commit has happened — FastAPI's get_db dependency
      exits its `async with AsyncSessionLocal()` block on the
      propagating exception, and AsyncSession.close() implicitly
      rolls back any uncommitted work. Critically, this also means
      the *previous* chunk set (from an earlier successful /process
      call) is still intact: _replace_chunks() is only called after
      embed_texts() has already succeeded, so a reprocessing attempt
      whose embedding call fails never reaches the point where old
      chunks would be deleted. The previously committed DocumentText
      and DocumentChunk rows (if any) are therefore left exactly as
      they were — a document never ends up with new text paired with
      stale, missing, or embedding-less chunks.
    """
    document_text = await document_text_service._upsert_document_text(db, document=document)

    chunk_texts = chunk_service.chunk_text(document_text.content)

    embeddings: list[list[float]] = []
    if chunk_texts:
        embeddings = await embedding_service.embed_texts(chunk_texts)

    chunks = await chunk_service._replace_chunks(
        db, document_text=document_text, chunk_texts=chunk_texts, embeddings=embeddings
    )

    await db.commit()
    await db.refresh(document_text)
    for chunk in chunks:
        await db.refresh(chunk)

    return document_text, chunks
