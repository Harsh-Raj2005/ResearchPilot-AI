"""
Tests for the DocumentText model.

Document Text Extraction Checkpoint 3 scope: model/database-level
only — no HTTP, no service layer, no parser wiring. Mirrors
test_document_model.py's style: reuses the existing db_session
fixture from conftest.py, no new test infrastructure needed.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_text import DocumentText
from app.services import auth_service


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"doctext_{suffix}@example.com",
        username=f"doctext{suffix}",
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


# --- Test 1: insertion works ---


async def test_document_text_can_be_inserted_and_retrieved(db_session: AsyncSession):
    user = await _make_user(db_session, "insert")
    document = await _make_document(db_session, user.id)

    document_text = DocumentText(document_id=document.id, content="Some extracted text.")
    db_session.add(document_text)
    await db_session.commit()
    await db_session.refresh(document_text)

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.id == document_text.id)
    )
    fetched = result.scalar_one()

    assert fetched.id is not None
    assert fetched.document_id == document.id
    assert fetched.content == "Some extracted text."
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


# --- Test 2: empty extracted text is valid ---


async def test_document_text_allows_empty_content(db_session: AsyncSession):
    """
    A valid PDF with no extractable text is a legitimate parse_service
    outcome (see parse_service.extract_text's documented contract) —
    this must persist successfully, not be conflated with a failure.
    """
    user = await _make_user(db_session, "empty")
    document = await _make_document(db_session, user.id)

    document_text = DocumentText(document_id=document.id, content="")
    db_session.add(document_text)
    await db_session.commit()
    await db_session.refresh(document_text)

    assert document_text.content == ""


# --- Test 3: one-to-zero-or-one uniqueness, enforced at the DB level ---


async def test_document_text_document_id_must_be_unique(db_session: AsyncSession):
    user = await _make_user(db_session, "dupe")
    document = await _make_document(db_session, user.id)

    first = DocumentText(document_id=document.id, content="First extraction.")
    db_session.add(first)
    await db_session.commit()

    second = DocumentText(document_id=document.id, content="Second attempt.")
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# --- Test 4: cascade delete ---


async def test_deleting_document_cascades_to_its_document_text(db_session: AsyncSession):
    user = await _make_user(db_session, "cascade")
    document = await _make_document(db_session, user.id)

    document_text = DocumentText(document_id=document.id, content="Will be cascaded away.")
    db_session.add(document_text)
    await db_session.commit()
    document_text_id = document_text.id

    await db_session.delete(document)
    await db_session.commit()

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.id == document_text_id)
    )
    assert result.scalar_one_or_none() is None


# --- Test 5: foreign key integrity ---


async def test_document_text_requires_a_real_document(db_session: AsyncSession):
    """
    A DocumentText cannot reference a nonexistent Document — enforced
    by the real FK constraint against the real Postgres test database
    this project's test suite already runs against (not SQLite, so
    this FK is genuinely enforced, not merely assumed).
    """
    document_text = DocumentText(document_id=uuid.uuid4(), content="Orphan attempt.")
    db_session.add(document_text)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# --- Test 6: normal, realistic content is preserved unchanged ---


async def test_document_text_preserves_realistic_multiline_content(db_session: AsyncSession):
    user = await _make_user(db_session, "multiline")
    document = await _make_document(db_session, user.id)

    realistic_content = "Page one heading.\nSome body text here.\n\nPage two heading.\nMore body text."
    document_text = DocumentText(document_id=document.id, content=realistic_content)
    db_session.add(document_text)
    await db_session.commit()
    await db_session.refresh(document_text)

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.id == document_text.id)
    )
    fetched = result.scalar_one()

    # Persisted exactly as given — no normalization at the persistence
    # layer; that's parse_service's job, not this model's.
    assert fetched.content == realistic_content
