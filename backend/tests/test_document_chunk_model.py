"""
Tests for the DocumentChunk model.

Document Chunking milestone scope: model/database-level only — no
HTTP, no service layer, no chunking algorithm. Mirrors
test_document_text_model.py's style exactly: reuses the existing
db_session fixture, no new test infrastructure.

Document Chunks -> Embeddings milestone: DocumentChunk.embedding is
now NOT NULL, so every DocumentChunk construction in this file
supplies one. New tests cover the embedding column specifically:
valid storage/retrieval, dimensionality, and NOT NULL enforcement.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import EMBEDDING_DIMENSIONS, DocumentChunk
from app.models.document_text import DocumentText
from app.services import auth_service

_SAMPLE_EMBEDDING = [0.1] * EMBEDDING_DIMENSIONS


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"docchunk_{suffix}@example.com",
        username=f"docchunk{suffix}",
        password="password123",
    )


async def _make_document(db_session: AsyncSession, user_id: uuid.UUID) -> Document:
    stored_name = f"{uuid.uuid4()}.pdf"
    document = Document(
        user_id=user_id,
        original_filename="research_paper.pdf",
        stored_filename=stored_name,
        content_type="application/pdf",
        file_size_bytes=123_456,
        storage_path=f"uploads/{stored_name}",
    )
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(document)
    return document


async def _make_document_text(db_session: AsyncSession, document_id: uuid.UUID) -> DocumentText:
    document_text = DocumentText(document_id=document_id, content="Some extracted text.")
    db_session.add(document_text)
    await db_session.commit()
    await db_session.refresh(document_text)
    return document_text


# --- Test 1: insertion works ---


async def test_document_chunk_can_be_inserted_and_retrieved(db_session: AsyncSession):
    user = await _make_user(db_session, "insert")
    document = await _make_document(db_session, user.id)
    document_text = await _make_document_text(db_session, document.id)

    chunk = DocumentChunk(
        document_text_id=document_text.id,
        chunk_index=0,
        content="Chunk one.",
        embedding=_SAMPLE_EMBEDDING,
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)

    result = await db_session.execute(select(DocumentChunk).where(DocumentChunk.id == chunk.id))
    fetched = result.scalar_one()

    assert fetched.id is not None
    assert fetched.document_text_id == document_text.id
    assert fetched.chunk_index == 0
    assert fetched.content == "Chunk one."
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


# --- Test 2: a DocumentText can have many chunks ---


async def test_document_text_can_have_many_chunks(db_session: AsyncSession):
    user = await _make_user(db_session, "many")
    document = await _make_document(db_session, user.id)
    document_text = await _make_document_text(db_session, document.id)

    for index in range(3):
        db_session.add(
            DocumentChunk(
                document_text_id=document_text.id,
                chunk_index=index,
                content=f"Chunk {index}.",
                embedding=_SAMPLE_EMBEDDING,
            )
        )
    await db_session.commit()

    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == document_text.id)
    )
    chunks = result.scalars().all()
    assert len(chunks) == 3
    assert sorted(c.chunk_index for c in chunks) == [0, 1, 2]


# --- Test 3: composite uniqueness (document_text_id, chunk_index), enforced at the DB level ---


async def test_document_chunk_index_must_be_unique_per_document_text(db_session: AsyncSession):
    user = await _make_user(db_session, "dupe")
    document = await _make_document(db_session, user.id)
    document_text = await _make_document_text(db_session, document.id)

    first = DocumentChunk(
        document_text_id=document_text.id,
        chunk_index=0,
        content="First.",
        embedding=_SAMPLE_EMBEDDING,
    )
    db_session.add(first)
    await db_session.commit()

    duplicate_index = DocumentChunk(
        document_text_id=document_text.id,
        chunk_index=0,
        content="Duplicate index.",
        embedding=_SAMPLE_EMBEDDING,
    )
    db_session.add(duplicate_index)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_same_chunk_index_is_allowed_across_different_document_texts(
    db_session: AsyncSession,
):
    """
    The uniqueness constraint is scoped to (document_text_id,
    chunk_index) together, not chunk_index alone — two different
    documents' chunk 0 must coexist without conflict.
    """
    user = await _make_user(db_session, "crossdoc")
    document_a = await _make_document(db_session, user.id)
    document_b = await _make_document(db_session, user.id)
    text_a = await _make_document_text(db_session, document_a.id)
    text_b = await _make_document_text(db_session, document_b.id)

    db_session.add(
        DocumentChunk(
            document_text_id=text_a.id,
            chunk_index=0,
            content="A chunk 0.",
            embedding=_SAMPLE_EMBEDDING,
        )
    )
    db_session.add(
        DocumentChunk(
            document_text_id=text_b.id,
            chunk_index=0,
            content="B chunk 0.",
            embedding=_SAMPLE_EMBEDDING,
        )
    )
    await db_session.commit()  # must not raise


# --- Test 4: cascade delete ---


async def test_deleting_document_text_cascades_to_its_chunks(db_session: AsyncSession):
    user = await _make_user(db_session, "cascade")
    document = await _make_document(db_session, user.id)
    document_text = await _make_document_text(db_session, document.id)

    chunk = DocumentChunk(
        document_text_id=document_text.id,
        chunk_index=0,
        content="Will cascade.",
        embedding=_SAMPLE_EMBEDDING,
    )
    db_session.add(chunk)
    await db_session.commit()
    chunk_id = chunk.id

    await db_session.delete(document_text)
    await db_session.commit()

    result = await db_session.execute(select(DocumentChunk).where(DocumentChunk.id == chunk_id))
    assert result.scalar_one_or_none() is None


async def test_deleting_document_cascades_through_document_text_to_chunks(
    db_session: AsyncSession,
):
    """The full cascade chain: deleting Document removes DocumentText,
    which in turn removes DocumentChunk rows — exercised end to end."""
    user = await _make_user(db_session, "fullcascade")
    document = await _make_document(db_session, user.id)
    document_text = await _make_document_text(db_session, document.id)

    chunk = DocumentChunk(
        document_text_id=document_text.id,
        chunk_index=0,
        content="Full chain.",
        embedding=_SAMPLE_EMBEDDING,
    )
    db_session.add(chunk)
    await db_session.commit()
    chunk_id = chunk.id

    await db_session.delete(document)
    await db_session.commit()

    result = await db_session.execute(select(DocumentChunk).where(DocumentChunk.id == chunk_id))
    assert result.scalar_one_or_none() is None


# --- Test 5: foreign key integrity ---


async def test_document_chunk_requires_a_real_document_text(db_session: AsyncSession):
    """
    A DocumentChunk cannot reference a nonexistent DocumentText —
    enforced by the real FK constraint against the real Postgres test
    database, same as test_document_text_model.py's equivalent test.
    """
    chunk = DocumentChunk(
        document_text_id=uuid.uuid4(),
        chunk_index=0,
        content="Orphan attempt.",
        embedding=_SAMPLE_EMBEDDING,
    )
    db_session.add(chunk)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# --- Test 6: embedding column — valid storage/retrieval and dimensionality ---


async def test_document_chunk_embedding_persists_and_retrieves_correctly(
    db_session: AsyncSession,
):
    user = await _make_user(db_session, "embedstore")
    document = await _make_document(db_session, user.id)
    document_text = await _make_document_text(db_session, document.id)

    embedding = [float(i) / 1000 for i in range(EMBEDDING_DIMENSIONS)]
    chunk = DocumentChunk(
        document_text_id=document_text.id,
        chunk_index=0,
        content="Embedded chunk.",
        embedding=embedding,
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)

    result = await db_session.execute(select(DocumentChunk).where(DocumentChunk.id == chunk.id))
    fetched = result.scalar_one()

    assert len(fetched.embedding) == EMBEDDING_DIMENSIONS
    # pgvector round-trips as floats; compare with a small tolerance
    # rather than exact equality to avoid float-precision flakiness.
    assert all(abs(a - b) < 1e-6 for a, b in zip(fetched.embedding, embedding))


# --- Test 7: embedding is NOT NULL ---


async def test_document_chunk_embedding_is_not_nullable(db_session: AsyncSession):
    user = await _make_user(db_session, "notnull")
    document = await _make_document(db_session, user.id)
    document_text = await _make_document_text(db_session, document.id)

    # Deliberately omit `embedding` — the column has no Python-side
    # default, so this must fail at the database level (NOT NULL),
    # confirmed against real Postgres, not just asserted by the type
    # annotation.
    chunk = DocumentChunk(document_text_id=document_text.id, chunk_index=0, content="No embedding.")
    db_session.add(chunk)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
