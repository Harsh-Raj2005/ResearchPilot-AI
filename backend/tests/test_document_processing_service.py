"""
Tests for app.services.document_processing_service.

This is the critical test coverage the Document Chunking transaction
redesign exists for: process_document() must persist DocumentText and
DocumentChunk rows atomically — either both succeed and commit
together, or neither is durably changed.

Real Document/DocumentText/DocumentChunk setup throughout (via
document_service.create_document(), same convention as
test_document_text_service.py) — no mocking of storage_service or
parse_service. The one exception, explained at its point of use: the
"chunk persistence fails after text has been staged" scenario has no
natural real-infrastructure failure point available given the current
schema (no CHECK constraint or other DB-level failure mode sits
between the two flushes), so that one case monkeypatches
chunk_service.chunk_text — a pure function — to raise deterministically,
simulating a genuine mid-pipeline failure without mocking the database
or either service's actual persistence logic.
"""
import pymupdf
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document_chunk import DocumentChunk
from app.models.document_text import DocumentText
from app.services import (
    auth_service,
    chunk_service,
    document_processing_service,
    document_service,
    parse_service,
)


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    yield tmp_path


def _make_pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    if text:
        page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"docproc_{suffix}@example.com",
        username=f"docproc{suffix}",
        password="password123",
    )


async def _make_document(db_session: AsyncSession, suffix: str, *, text: str, filename: str = "paper.pdf"):
    user = await _make_user(db_session, suffix)
    return await document_service.create_document(
        db_session,
        user_id=user.id,
        content=_make_pdf_bytes(text),
        original_filename=filename,
        content_type="application/pdf",
    )


async def _chunk_count(db_session: AsyncSession, document_text_id) -> int:
    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == document_text_id)
    )
    return len(result.scalars().all())


# --- 1. Successful processing persists both text and chunks together ---


async def test_process_document_persists_text_and_chunks_together(db_session: AsyncSession):
    document = await _make_document(db_session, "success", text="Hello, ResearchPilot.")

    document_text, chunks = await document_processing_service.process_document(
        db_session, document=document
    )

    assert document_text.content == "Hello, ResearchPilot."
    assert len(chunks) == 1
    assert chunks[0].content == "Hello, ResearchPilot."
    assert chunks[0].chunk_index == 0

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    assert result.scalar_one().content == "Hello, ResearchPilot."
    assert await _chunk_count(db_session, document_text.id) == 1


# --- 2. Parse failure before any write: nothing changes ---


async def test_process_document_parse_failure_persists_nothing(db_session: AsyncSession):
    document = await _make_document(db_session, "corrupt", text="temp", filename="corrupt.pdf")
    # Overwrite the real PDF on disk with garbage bytes after upload —
    # same "manipulate real state out from under an in-flight object"
    # technique already used by this project's other failure tests.
    from pathlib import Path

    Path(document.storage_path).write_bytes(b"not a real pdf")

    with pytest.raises(parse_service.ParseError):
        await document_processing_service.process_document(db_session, document=document)

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    assert result.scalar_one_or_none() is None


# --- 3. Reprocessing replaces both text and chunks atomically ---


async def test_process_document_reprocessing_replaces_text_and_chunks(db_session: AsyncSession):
    document = await _make_document(db_session, "reprocess", text="Short original text.")

    first_text, first_chunks = await document_processing_service.process_document(
        db_session, document=document
    )
    assert first_text.content == "Short original text."

    # Simulate a real reprocess with different (longer) content by
    # overwriting the stored file, exactly as a real re-upload-then-
    # reprocess would leave the file on disk.
    from pathlib import Path

    longer_pdf = pymupdf.open()
    page = longer_pdf.new_page()
    page.insert_text((72, 72), "A materially different, longer piece of text.")
    Path(document.storage_path).write_bytes(longer_pdf.tobytes())
    longer_pdf.close()

    second_text, second_chunks = await document_processing_service.process_document(
        db_session, document=document
    )

    # Same DocumentText row (updated in place), not a new one.
    assert second_text.id == first_text.id
    assert second_text.content == "A materially different, longer piece of text."

    # Chunks fully replaced: no stale rows survive, no duplicates.
    persisted_count = await _chunk_count(db_session, first_text.id)
    assert persisted_count == len(second_chunks)
    first_chunk_ids = {c.id for c in first_chunks}
    persisted_ids = {c.id for c in second_chunks}
    assert first_chunk_ids.isdisjoint(persisted_ids)


# --- 4. THE critical case: chunk persistence fails after text was staged ---


async def test_process_document_chunk_failure_leaves_previous_state_intact(
    db_session: AsyncSession, monkeypatch
):
    """
    The core guarantee this milestone exists for: if chunking fails
    after the DocumentText upsert has been flushed (but not
    committed), the whole operation must leave the database exactly
    as it was before the call — no "new text, old/missing chunks"
    inconsistency ever becomes visible.

    chunk_service.chunk_text (a pure function, not the database or
    either service's persistence logic) is monkeypatched to raise,
    since no natural real-infrastructure failure exists at this exact
    point given the current schema — this simulates a genuine
    mid-pipeline failure deterministically without mocking any
    persistence code.
    """
    document = await _make_document(db_session, "atomicfail", text="Original stable text.")
    document_id = document.id

    # Establish a known-good, already-committed baseline first.
    original_text, original_chunks = await document_processing_service.process_document(
        db_session, document=document
    )
    assert original_text.content == "Original stable text."
    assert len(original_chunks) == 1
    original_chunk_id = original_chunks[0].id  # captured before the rollback below expires it
    original_text_id = original_text.id

    def _boom(content: str):
        raise RuntimeError("simulated chunking failure")

    monkeypatch.setattr(chunk_service, "chunk_text", _boom)

    # Change the underlying file so a real reprocess would produce
    # different text, proving the failure genuinely prevents the
    # *new* text from becoming visible too.
    from pathlib import Path

    changed_pdf = pymupdf.open()
    page = changed_pdf.new_page()
    page.insert_text((72, 72), "This text must NOT end up persisted.")
    Path(document.storage_path).write_bytes(changed_pdf.tobytes())
    changed_pdf.close()

    with pytest.raises(RuntimeError):
        await document_processing_service.process_document(db_session, document=document)

    # The session's uncommitted work must be rolled back for this
    # assertion to be meaningful against the real committed state —
    # matching what FastAPI's get_db dependency does automatically on
    # a propagating exception (AsyncSession.close() -> implicit
    # rollback). This test does explicitly what get_db does implicitly.
    await db_session.rollback()

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document_id)
    )
    persisted_text = result.scalar_one()
    assert persisted_text.content == "Original stable text."  # NOT the new text

    assert await _chunk_count(db_session, original_text_id) == 1
    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == original_text_id)
    )
    persisted_chunk = result.scalar_one()
    assert persisted_chunk.content == "Original stable text."  # the OLD chunk, untouched
    assert persisted_chunk.id == original_chunk_id  # literally the same row


# --- 5. Empty extracted text: zero chunks persist correctly ---


async def test_process_document_empty_text_persists_zero_chunks(db_session: AsyncSession):
    document = await _make_document(db_session, "emptytext", text="")

    document_text, chunks = await document_processing_service.process_document(
        db_session, document=document
    )

    assert document_text.content == ""
    assert chunks == []
    assert await _chunk_count(db_session, document_text.id) == 0
