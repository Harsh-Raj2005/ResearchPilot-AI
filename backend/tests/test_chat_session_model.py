"""
Tests for the ChatSession and ChatMessage models.

Chat Persistence milestone scope: model/database-level only — no
HTTP, no service layer. Mirrors test_document_chunk_model.py's style
exactly: reuses the existing db_session fixture, no new test
infrastructure.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.document import Document
from app.services import auth_service


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"chatmodel_{suffix}@example.com",
        username=f"chatmodel{suffix}",
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


# --- 1. ChatSession insertion works ---


async def test_chat_session_can_be_inserted_and_retrieved(db_session: AsyncSession):
    user = await _make_user(db_session, "insert")
    document = await _make_document(db_session, user.id)

    session = ChatSession(document_id=document.id)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    result = await db_session.execute(select(ChatSession).where(ChatSession.id == session.id))
    fetched = result.scalar_one()

    assert fetched.id is not None
    assert fetched.document_id == document.id
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


# --- 2. A Document can have many chat sessions ---


async def test_document_can_have_many_chat_sessions(db_session: AsyncSession):
    user = await _make_user(db_session, "many")
    document = await _make_document(db_session, user.id)

    for _ in range(3):
        db_session.add(ChatSession(document_id=document.id))
    await db_session.commit()

    result = await db_session.execute(
        select(ChatSession).where(ChatSession.document_id == document.id)
    )
    sessions = result.scalars().all()
    assert len(sessions) == 3


# --- 3. ChatSession cascade delete ---


async def test_deleting_document_cascades_to_chat_sessions(db_session: AsyncSession):
    user = await _make_user(db_session, "cascade")
    document = await _make_document(db_session, user.id)

    session = ChatSession(document_id=document.id)
    db_session.add(session)
    await db_session.commit()
    session_id = session.id

    await db_session.delete(document)
    await db_session.commit()

    result = await db_session.execute(select(ChatSession).where(ChatSession.id == session_id))
    assert result.scalar_one_or_none() is None


# --- 4. ChatSession FK integrity ---


async def test_chat_session_requires_a_real_document(db_session: AsyncSession):
    session = ChatSession(document_id=uuid.uuid4())
    db_session.add(session)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# --- 5. ChatMessage insertion works ---


async def test_chat_message_can_be_inserted_and_retrieved(db_session: AsyncSession):
    user = await _make_user(db_session, "msginsert")
    document = await _make_document(db_session, user.id)
    session = ChatSession(document_id=document.id)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    message = ChatMessage(
        chat_session_id=session.id, role="user", content="What is this paper about?", sequence_number=0
    )
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)

    result = await db_session.execute(select(ChatMessage).where(ChatMessage.id == message.id))
    fetched = result.scalar_one()

    assert fetched.chat_session_id == session.id
    assert fetched.role == "user"
    assert fetched.content == "What is this paper about?"
    assert fetched.sequence_number == 0
    assert fetched.created_at is not None


# --- 6. A ChatSession can have many messages, in order ---


async def test_chat_session_can_have_many_messages(db_session: AsyncSession):
    user = await _make_user(db_session, "manymsg")
    document = await _make_document(db_session, user.id)
    session = ChatSession(document_id=document.id)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    for i in range(4):
        role = "user" if i % 2 == 0 else "assistant"
        db_session.add(
            ChatMessage(
                chat_session_id=session.id, role=role, content=f"message {i}", sequence_number=i
            )
        )
    await db_session.commit()

    result = await db_session.execute(
        select(ChatMessage)
        .where(ChatMessage.chat_session_id == session.id)
        .order_by(ChatMessage.sequence_number.asc())
    )
    messages = result.scalars().all()
    assert [m.sequence_number for m in messages] == [0, 1, 2, 3]
    assert [m.content for m in messages] == ["message 0", "message 1", "message 2", "message 3"]


# --- 7. ChatMessage cascade delete ---


async def test_deleting_chat_session_cascades_to_messages(db_session: AsyncSession):
    user = await _make_user(db_session, "msgcascade")
    document = await _make_document(db_session, user.id)
    session = ChatSession(document_id=document.id)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    message = ChatMessage(
        chat_session_id=session.id, role="user", content="will cascade", sequence_number=0
    )
    db_session.add(message)
    await db_session.commit()
    message_id = message.id

    await db_session.delete(session)
    await db_session.commit()

    result = await db_session.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    assert result.scalar_one_or_none() is None


async def test_deleting_document_cascades_through_session_to_messages(db_session: AsyncSession):
    """The full cascade chain: deleting Document removes ChatSession,
    which in turn removes ChatMessage rows — exercised end to end."""
    user = await _make_user(db_session, "fullcascade")
    document = await _make_document(db_session, user.id)
    session = ChatSession(document_id=document.id)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    message = ChatMessage(
        chat_session_id=session.id, role="user", content="full chain", sequence_number=0
    )
    db_session.add(message)
    await db_session.commit()
    message_id = message.id

    await db_session.delete(document)
    await db_session.commit()

    result = await db_session.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    assert result.scalar_one_or_none() is None


# --- 8. ChatMessage FK integrity ---


async def test_chat_message_requires_a_real_chat_session(db_session: AsyncSession):
    message = ChatMessage(
        chat_session_id=uuid.uuid4(), role="user", content="orphan attempt", sequence_number=0
    )
    db_session.add(message)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# --- 9. Composite uniqueness (chat_session_id, sequence_number) ---


async def test_sequence_number_must_be_unique_per_session(db_session: AsyncSession):
    user = await _make_user(db_session, "dupeseq")
    document = await _make_document(db_session, user.id)
    session = ChatSession(document_id=document.id)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    first = ChatMessage(chat_session_id=session.id, role="user", content="first", sequence_number=0)
    db_session.add(first)
    await db_session.commit()

    duplicate = ChatMessage(
        chat_session_id=session.id, role="assistant", content="dup", sequence_number=0
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_same_sequence_number_allowed_across_different_sessions(db_session: AsyncSession):
    """
    The uniqueness constraint is scoped to (chat_session_id,
    sequence_number) together, not sequence_number alone — two
    different sessions' message 0 must coexist without conflict.
    """
    user = await _make_user(db_session, "crosssession")
    document = await _make_document(db_session, user.id)
    session_a = ChatSession(document_id=document.id)
    session_b = ChatSession(document_id=document.id)
    db_session.add_all([session_a, session_b])
    await db_session.commit()
    await db_session.refresh(session_a)
    await db_session.refresh(session_b)

    db_session.add(
        ChatMessage(chat_session_id=session_a.id, role="user", content="A msg 0", sequence_number=0)
    )
    db_session.add(
        ChatMessage(chat_session_id=session_b.id, role="user", content="B msg 0", sequence_number=0)
    )
    await db_session.commit()  # must not raise
