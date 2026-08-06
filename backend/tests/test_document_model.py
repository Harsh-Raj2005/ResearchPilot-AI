"""
Tests for the Document model.

Task 3B Checkpoint 1 scope: model-level only, no HTTP, no service
layer — mirrors how test_deps.py tested get_current_user directly
before any route existed. Reuses the existing db_session fixture from
conftest.py; no new test infrastructure needed.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.services import auth_service


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"docmodel_{suffix}@example.com",
        username=f"docmodel{suffix}",
        password="password123",
    )


def _sample_document_kwargs(user_id: uuid.UUID, stored_filename: str) -> dict:
    return dict(
        user_id=user_id,
        original_filename="research_paper.pdf",
        stored_filename=stored_filename,
        content_type="application/pdf",
        file_size_bytes=123_456,
        storage_path=f"uploads/{stored_filename}",
    )


async def test_document_can_be_inserted_and_retrieved(db_session: AsyncSession):
    user = await _make_user(db_session, "insert")
    stored_name = f"{uuid.uuid4()}.pdf"

    document = Document(**_sample_document_kwargs(user.id, stored_name))
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(document)

    result = await db_session.execute(select(Document).where(Document.id == document.id))
    fetched = result.scalar_one()

    assert fetched.user_id == user.id
    assert fetched.original_filename == "research_paper.pdf"
    assert fetched.stored_filename == stored_name
    assert fetched.content_type == "application/pdf"
    assert fetched.file_size_bytes == 123_456
    assert fetched.storage_path == f"uploads/{stored_name}"


async def test_document_timestamps_are_populated_automatically(db_session: AsyncSession):
    user = await _make_user(db_session, "timestamps")
    stored_name = f"{uuid.uuid4()}.pdf"

    document = Document(**_sample_document_kwargs(user.id, stored_name))
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(document)

    assert document.created_at is not None
    assert document.updated_at is not None


async def test_document_stored_filename_must_be_unique(db_session: AsyncSession):
    user = await _make_user(db_session, "dupfilename")
    stored_name = f"{uuid.uuid4()}.pdf"

    first = Document(**_sample_document_kwargs(user.id, stored_name))
    db_session.add(first)
    await db_session.commit()

    second = Document(**_sample_document_kwargs(user.id, stored_name))
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_deleting_user_cascades_to_their_documents(db_session: AsyncSession):
    user = await _make_user(db_session, "cascade")
    stored_name = f"{uuid.uuid4()}.pdf"

    document = Document(**_sample_document_kwargs(user.id, stored_name))
    db_session.add(document)
    await db_session.commit()
    document_id = document.id

    await db_session.delete(user)
    await db_session.commit()

    result = await db_session.execute(select(Document).where(Document.id == document_id))
    assert result.scalar_one_or_none() is None


async def test_document_requires_user_id(db_session: AsyncSession):
    stored_name = f"{uuid.uuid4()}.pdf"
    document = Document(
        original_filename="orphan.pdf",
        stored_filename=stored_name,
        content_type="application/pdf",
        file_size_bytes=100,
        storage_path=f"uploads/{stored_name}",
    )
    db_session.add(document)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
