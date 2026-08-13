"""
Tests for app.services.document_processing_service.

This is the critical test coverage the Document Chunking transaction
redesign exists for: process_document() must persist DocumentText and
DocumentChunk rows atomically — either both succeed and commit
together, or neither is durably changed.

Document Chunks -> Embeddings milestone: extends that same atomicity
guarantee to embeddings. embedding_service.embed_texts() is
monkeypatched to a deterministic fake in every test via an autouse
fixture — no real OpenAI call is ever made here. Tests that need to
exercise embedding-failure behavior override that fixture's patch
locally.

Real Document/DocumentText/DocumentChunk setup throughout (via
document_service.create_document(), same convention as
test_document_text_service.py) — no mocking of storage_service or
parse_service. The "chunk_text() failure" scenario has no natural
real-infrastructure failure point available given the current schema
(no CHECK constraint or other DB-level failure mode sits at that
exact point), so that one case monkeypatches chunk_service.chunk_text
— a pure function — to raise deterministically, simulating a genuine
mid-pipeline failure without mocking the database or either service's
actual persistence logic.
"""
from pathlib import Path

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
    embedding_service,
    parse_service,
)

_EMBEDDING_DIM = 1536


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    yield tmp_path


@pytest.fixture(autouse=True)
def _mock_embed_texts(monkeypatch):
    """
    No test in this file ever calls the real OpenAI API. Each call to
    embed_texts() gets a distinguishable fake vector (offset by a
    per-call counter), so tests that reprocess a document can verify
    the *second* call's embeddings genuinely differ from the first's
    — proving embeddings are recomputed, not stale/copied.
    """
    call_counter = {"count": 0}

    async def _fake_embed_texts(texts: list[str]) -> list[list[float]]:
        call_counter["count"] += 1
        offset = call_counter["count"] * 1000.0
        return [[offset + i] * _EMBEDDING_DIM for i in range(len(texts))]

    monkeypatch.setattr(embedding_service, "embed_texts", _fake_embed_texts)
    yield _fake_embed_texts


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


# --- 1. Successful processing persists text, chunks, and embeddings together ---


async def test_process_document_persists_text_chunks_and_embeddings_together(
    db_session: AsyncSession,
):
    document = await _make_document(db_session, "success", text="Hello, ResearchPilot.")

    document_text, chunks = await document_processing_service.process_document(
        db_session, document=document
    )

    assert document_text.content == "Hello, ResearchPilot."
    assert len(chunks) == 1
    assert chunks[0].content == "Hello, ResearchPilot."
    assert chunks[0].chunk_index == 0
    assert len(chunks[0].embedding) == _EMBEDDING_DIM

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
    Path(document.storage_path).write_bytes(b"not a real pdf")

    with pytest.raises(parse_service.ParseError):
        await document_processing_service.process_document(db_session, document=document)

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    assert result.scalar_one_or_none() is None


# --- 3. Reprocessing replaces text, chunks, and embeddings atomically ---


async def test_process_document_reprocessing_replaces_text_chunks_and_embeddings(
    db_session: AsyncSession,
):
    document = await _make_document(db_session, "reprocess", text="Short original text.")

    first_text, first_chunks = await document_processing_service.process_document(
        db_session, document=document
    )
    assert first_text.content == "Short original text."
    first_embedding = list(first_chunks[0].embedding)

    # Simulate a real reprocess with different (longer) content by
    # overwriting the stored file, exactly as a real re-upload-then-
    # reprocess would leave the file on disk.
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

    # New chunks have genuinely new embeddings, not the old, stale ones.
    second_embedding = list(second_chunks[0].embedding)
    assert second_embedding != first_embedding


# --- 4. chunk_text() failure: nothing changes (a different failure point than embedding) ---


async def test_process_document_chunking_failure_leaves_previous_state_intact(
    db_session: AsyncSession, monkeypatch
):
    """
    If chunking itself fails (before embed_texts() is ever called),
    the whole operation must leave the database exactly as it was
    before the call — no "new text, old/missing chunks" inconsistency
    ever becomes visible.

    chunk_service.chunk_text (a pure function, not the database or
    either service's persistence logic) is monkeypatched to raise,
    since no natural real-infrastructure failure exists at this exact
    point given the current schema.
    """
    document = await _make_document(db_session, "chunkfail", text="Original stable text.")
    document_id = document.id

    original_text, original_chunks = await document_processing_service.process_document(
        db_session, document=document
    )
    assert original_text.content == "Original stable text."
    assert len(original_chunks) == 1
    original_chunk_id = original_chunks[0].id
    original_text_id = original_text.id

    def _boom(content: str):
        raise RuntimeError("simulated chunking failure")

    monkeypatch.setattr(chunk_service, "chunk_text", _boom)

    changed_pdf = pymupdf.open()
    page = changed_pdf.new_page()
    page.insert_text((72, 72), "This text must NOT end up persisted.")
    Path(document.storage_path).write_bytes(changed_pdf.tobytes())
    changed_pdf.close()

    with pytest.raises(RuntimeError):
        await document_processing_service.process_document(db_session, document=document)

    # Matches what FastAPI's get_db dependency does automatically on a
    # propagating exception (AsyncSession.close() -> implicit rollback).
    await db_session.rollback()

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document_id)
    )
    persisted_text = result.scalar_one()
    assert persisted_text.content == "Original stable text."

    assert await _chunk_count(db_session, original_text_id) == 1
    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == original_text_id)
    )
    persisted_chunk = result.scalar_one()
    assert persisted_chunk.content == "Original stable text."
    assert persisted_chunk.id == original_chunk_id


# --- 5. Empty extracted text: zero chunks persist correctly, no embedding call made ---


async def test_process_document_empty_text_persists_zero_chunks_no_embedding_call(
    db_session: AsyncSession, monkeypatch
):
    call_count = {"count": 0}

    async def _counting_embed_texts(texts):
        call_count["count"] += 1
        return []

    monkeypatch.setattr(embedding_service, "embed_texts", _counting_embed_texts)

    document = await _make_document(db_session, "emptytext", text="")

    document_text, chunks = await document_processing_service.process_document(
        db_session, document=document
    )

    assert document_text.content == ""
    assert chunks == []
    assert await _chunk_count(db_session, document_text.id) == 0
    assert call_count["count"] == 0  # embed_texts must never be called for zero chunks


# --- 6. Embedding failure on first processing: nothing persists ---


async def test_process_document_embedding_failure_on_first_processing_persists_nothing(
    db_session: AsyncSession, monkeypatch
):
    document = await _make_document(db_session, "embedfailfirst", text="Some real text.")
    document_id = document.id

    async def _failing_embed_texts(texts):
        raise embedding_service.EmbeddingProviderError("simulated provider failure")

    monkeypatch.setattr(embedding_service, "embed_texts", _failing_embed_texts)

    with pytest.raises(embedding_service.EmbeddingProviderError):
        await document_processing_service.process_document(db_session, document=document)

    await db_session.rollback()

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document_id)
    )
    assert result.scalar_one_or_none() is None

    result = await db_session.execute(select(DocumentChunk))
    all_chunks_for_this_run = [
        c for c in result.scalars().all()
    ]
    # No DocumentText exists for this document, so no chunk could
    # legitimately reference it either — nothing was left behind.
    assert all(c.document_text_id != document_id for c in all_chunks_for_this_run)


# --- 7. THE most important test: embedding failure during reprocessing preserves prior state ---


async def test_process_document_embedding_failure_during_reprocessing_leaves_previous_state_intact(
    db_session: AsyncSession, monkeypatch
):
    """
    This is the critical case: a document is already successfully
    processed (committed DocumentText + DocumentChunk + embeddings).
    Reprocessing is attempted with new content, but the embedding
    call fails. The previously committed DocumentText, chunks, and
    their embeddings must remain completely unchanged — old chunks
    must NOT be deleted before new embeddings have successfully been
    generated (this is exactly why _replace_chunks() is only called
    after embed_texts() has already returned successfully).
    """
    document = await _make_document(db_session, "embedfailreprocess", text="Original stable text.")
    document_id = document.id

    original_text, original_chunks = await document_processing_service.process_document(
        db_session, document=document
    )
    assert original_text.content == "Original stable text."
    assert len(original_chunks) == 1
    original_text_id = original_text.id
    original_chunk_id = original_chunks[0].id
    original_embedding = list(original_chunks[0].embedding)

    async def _failing_embed_texts(texts):
        raise embedding_service.EmbeddingProviderError("simulated provider failure")

    monkeypatch.setattr(embedding_service, "embed_texts", _failing_embed_texts)

    # Change the underlying file so a successful reprocess would have
    # produced different text — proving the failure genuinely
    # prevents the *new* text (and new chunks) from becoming visible.
    changed_pdf = pymupdf.open()
    page = changed_pdf.new_page()
    page.insert_text((72, 72), "This text must NOT end up persisted.")
    Path(document.storage_path).write_bytes(changed_pdf.tobytes())
    changed_pdf.close()

    with pytest.raises(embedding_service.EmbeddingProviderError):
        await document_processing_service.process_document(db_session, document=document)

    await db_session.rollback()

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document_id)
    )
    persisted_text = result.scalar_one()
    assert persisted_text.content == "Original stable text."  # NOT the new text
    assert persisted_text.id == original_text_id

    assert await _chunk_count(db_session, original_text_id) == 1
    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == original_text_id)
    )
    persisted_chunk = result.scalar_one()
    assert persisted_chunk.id == original_chunk_id  # literally the same row
    assert persisted_chunk.content == "Original stable text."  # the OLD chunk, untouched
    assert list(persisted_chunk.embedding) == pytest.approx(original_embedding)
