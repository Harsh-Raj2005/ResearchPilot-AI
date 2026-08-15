"""
Tests for app.services.chat_session_service.

Real Postgres throughout — no mocking of the database. Mirrors
test_document_processing_service.py's atomicity-testing technique for
_stage_message()'s own dedicated test below.
"""
import uuid

import pymupdf
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat_session import ChatSession
from app.services import auth_service, chat_session_service, document_service


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    yield tmp_path


def _make_pdf_bytes(text: str = "placeholder") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"chatsvc_{suffix}@example.com",
        username=f"chatsvc{suffix}",
        password="password123",
    )


async def _make_document(db_session: AsyncSession, suffix: str):
    user = await _make_user(db_session, suffix)
    return await document_service.create_document(
        db_session,
        user_id=user.id,
        content=_make_pdf_bytes(),
        original_filename="paper.pdf",
        content_type="application/pdf",
    )


# --- Session creation ---


async def test_create_session_creates_session_for_document(db_session: AsyncSession):
    document = await _make_document(db_session, "create")

    session = await chat_session_service.create_session(db_session, document=document)

    assert session.id is not None
    assert session.document_id == document.id
    assert session.created_at is not None


async def test_create_session_generates_unique_ids(db_session: AsyncSession):
    document = await _make_document(db_session, "unique")

    session_a = await chat_session_service.create_session(db_session, document=document)
    session_b = await chat_session_service.create_session(db_session, document=document)

    assert session_a.id != session_b.id


# --- Session listing ---


async def test_list_sessions_returns_sessions_for_document(db_session: AsyncSession):
    document = await _make_document(db_session, "list")
    session_a = await chat_session_service.create_session(db_session, document=document)
    session_b = await chat_session_service.create_session(db_session, document=document)

    sessions = await chat_session_service.list_sessions_for_document(db_session, document=document)

    assert {s.id for s in sessions} == {session_a.id, session_b.id}


async def test_list_sessions_excludes_another_documents_sessions(db_session: AsyncSession):
    document_a = await _make_document(db_session, "isoA")
    document_b = await _make_document(db_session, "isoB")
    session_a = await chat_session_service.create_session(db_session, document=document_a)
    await chat_session_service.create_session(db_session, document=document_b)

    sessions = await chat_session_service.list_sessions_for_document(db_session, document=document_a)

    assert [s.id for s in sessions] == [session_a.id]


async def test_list_sessions_ordered_newest_first(db_session: AsyncSession):
    document = await _make_document(db_session, "order")
    first = await chat_session_service.create_session(db_session, document=document)
    second = await chat_session_service.create_session(db_session, document=document)

    sessions = await chat_session_service.list_sessions_for_document(db_session, document=document)

    assert sessions[0].id == second.id
    assert sessions[1].id == first.id


async def test_list_sessions_pagination(db_session: AsyncSession):
    document = await _make_document(db_session, "page")
    for _ in range(5):
        await chat_session_service.create_session(db_session, document=document)

    page1 = await chat_session_service.list_sessions_for_document(
        db_session, document=document, skip=0, limit=2
    )
    page2 = await chat_session_service.list_sessions_for_document(
        db_session, document=document, skip=2, limit=2
    )

    assert len(page1) == 2
    assert len(page2) == 2
    assert {s.id for s in page1}.isdisjoint({s.id for s in page2})


# --- Session lookup ---


async def test_get_session_for_document_returns_correct_session(db_session: AsyncSession):
    document = await _make_document(db_session, "lookup")
    session = await chat_session_service.create_session(db_session, document=document)

    fetched = await chat_session_service.get_session_for_document(
        db_session, document=document, session_id=session.id
    )

    assert fetched is not None
    assert fetched.id == session.id


async def test_get_session_for_document_returns_none_for_nonexistent_session(
    db_session: AsyncSession,
):
    document = await _make_document(db_session, "missing")

    fetched = await chat_session_service.get_session_for_document(
        db_session, document=document, session_id=uuid.uuid4()
    )

    assert fetched is None


async def test_get_session_for_document_rejects_session_from_another_document(
    db_session: AsyncSession,
):
    document_a = await _make_document(db_session, "mismatchA")
    document_b = await _make_document(db_session, "mismatchB")
    session_a = await chat_session_service.create_session(db_session, document=document_a)

    fetched = await chat_session_service.get_session_for_document(
        db_session, document=document_b, session_id=session_a.id
    )

    assert fetched is None


# --- Message creation / sequence numbers ---


async def test_append_message_first_message_gets_sequence_zero(db_session: AsyncSession):
    document = await _make_document(db_session, "seq0")
    session = await chat_session_service.create_session(db_session, document=document)

    message = await chat_session_service.append_message(
        db_session, session=session, role="user", content="first"
    )

    assert message.sequence_number == 0


async def test_append_message_second_message_gets_next_sequence(db_session: AsyncSession):
    document = await _make_document(db_session, "seq1")
    session = await chat_session_service.create_session(db_session, document=document)

    first = await chat_session_service.append_message(
        db_session, session=session, role="user", content="first"
    )
    second = await chat_session_service.append_message(
        db_session, session=session, role="assistant", content="second"
    )

    assert first.sequence_number == 0
    assert second.sequence_number == 1


async def test_append_message_sequence_continues_correctly(db_session: AsyncSession):
    document = await _make_document(db_session, "seqcontinue")
    session = await chat_session_service.create_session(db_session, document=document)

    sequence_numbers = []
    for i in range(5):
        role = "user" if i % 2 == 0 else "assistant"
        message = await chat_session_service.append_message(
            db_session, session=session, role=role, content=f"message {i}"
        )
        sequence_numbers.append(message.sequence_number)

    assert sequence_numbers == [0, 1, 2, 3, 4]


async def test_append_message_user_and_assistant_roles(db_session: AsyncSession):
    document = await _make_document(db_session, "roles")
    session = await chat_session_service.create_session(db_session, document=document)

    user_message = await chat_session_service.append_message(
        db_session, session=session, role="user", content="question"
    )
    assistant_message = await chat_session_service.append_message(
        db_session, session=session, role="assistant", content="answer"
    )

    assert user_message.role == "user"
    assert assistant_message.role == "assistant"


# --- Message listing ---


async def test_list_messages_ordered_by_sequence_number(db_session: AsyncSession):
    document = await _make_document(db_session, "msgorder")
    session = await chat_session_service.create_session(db_session, document=document)
    for i in range(4):
        await chat_session_service.append_message(
            db_session, session=session, role="user", content=f"msg {i}"
        )

    messages = await chat_session_service.list_messages_for_session(db_session, session=session)

    assert [m.sequence_number for m in messages] == [0, 1, 2, 3]
    assert [m.content for m in messages] == ["msg 0", "msg 1", "msg 2", "msg 3"]


async def test_list_messages_only_returns_requested_sessions_messages(db_session: AsyncSession):
    document = await _make_document(db_session, "msgscope")
    session_a = await chat_session_service.create_session(db_session, document=document)
    session_b = await chat_session_service.create_session(db_session, document=document)

    await chat_session_service.append_message(
        db_session, session=session_a, role="user", content="A's message"
    )
    await chat_session_service.append_message(
        db_session, session=session_b, role="user", content="B's message"
    )

    messages_a = await chat_session_service.list_messages_for_session(db_session, session=session_a)

    assert len(messages_a) == 1
    assert messages_a[0].content == "A's message"


# --- Constraints ---


async def test_duplicate_sequence_number_in_same_session_rejected(db_session: AsyncSession):
    from sqlalchemy.exc import IntegrityError

    from app.models.chat_message import ChatMessage

    document = await _make_document(db_session, "dupeseq")
    session = await chat_session_service.create_session(db_session, document=document)
    await chat_session_service.append_message(
        db_session, session=session, role="user", content="first"
    )

    duplicate = ChatMessage(
        chat_session_id=session.id, role="assistant", content="dup", sequence_number=0
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_same_sequence_number_allowed_across_different_sessions(db_session: AsyncSession):
    document = await _make_document(db_session, "crosssession")
    session_a = await chat_session_service.create_session(db_session, document=document)
    session_b = await chat_session_service.create_session(db_session, document=document)

    message_a = await chat_session_service.append_message(
        db_session, session=session_a, role="user", content="A msg"
    )
    message_b = await chat_session_service.append_message(
        db_session, session=session_b, role="user", content="B msg"
    )

    assert message_a.sequence_number == message_b.sequence_number == 0


# --- Cascade ---


async def test_deleting_document_removes_its_sessions(db_session: AsyncSession):
    from sqlalchemy import select

    document = await _make_document(db_session, "cascadedoc")
    session = await chat_session_service.create_session(db_session, document=document)
    session_id = session.id

    document_row = await document_service.get_document_for_user(
        db_session, document_id=document.id, user_id=document.user_id
    )
    await db_session.delete(document_row)
    await db_session.commit()

    result = await db_session.execute(select(ChatSession).where(ChatSession.id == session_id))
    assert result.scalar_one_or_none() is None


async def test_deleting_session_removes_its_messages(db_session: AsyncSession):
    from sqlalchemy import select

    from app.models.chat_message import ChatMessage

    document = await _make_document(db_session, "cascademsg")
    session = await chat_session_service.create_session(db_session, document=document)
    message = await chat_session_service.append_message(
        db_session, session=session, role="user", content="will cascade"
    )
    message_id = message.id

    await db_session.delete(session)
    await db_session.commit()

    result = await db_session.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    assert result.scalar_one_or_none() is None


# --- Atomicity: the critical fix this milestone required ---


async def test_stage_message_does_not_commit_independently(db_session: AsyncSession):
    """
    _stage_message() must only flush, never commit — proving the
    atomicity fix: a message staged via _stage_message() and then
    rolled back must not survive, unlike append_message() (which
    commits on its own and is a separate, standalone entry point).
    """
    document = await _make_document(db_session, "stagerollback")
    session = await chat_session_service.create_session(db_session, document=document)
    session_id = session.id  # captured before the rollback below expires it

    await chat_session_service._stage_message(
        db_session, session=session, role="user", content="should not survive rollback"
    )
    await db_session.rollback()

    from sqlalchemy import select

    from app.models.chat_message import ChatMessage

    result = await db_session.execute(
        select(ChatMessage).where(ChatMessage.chat_session_id == session_id)
    )
    assert result.scalars().all() == []


async def test_stage_message_both_writes_visible_after_one_commit(db_session: AsyncSession):
    """
    The intended real usage: two _stage_message() calls followed by
    one commit persists both messages together.
    """
    document = await _make_document(db_session, "stagecommit")
    session = await chat_session_service.create_session(db_session, document=document)

    await chat_session_service._stage_message(
        db_session, session=session, role="user", content="question"
    )
    await chat_session_service._stage_message(
        db_session, session=session, role="assistant", content="answer"
    )
    await db_session.commit()

    messages = await chat_session_service.list_messages_for_session(db_session, session=session)
    assert [m.content for m in messages] == ["question", "answer"]
    assert [m.sequence_number for m in messages] == [0, 1]


async def test_stage_message_sequence_allocation_safe_under_concurrent_access(
    db_session: AsyncSession,
):
    """
    Regression test for the SELECT ... FOR UPDATE fix: two concurrent
    _stage_message() calls for the SAME session, each on its own
    independent database connection/transaction (simulating two
    concurrent HTTP requests), must never compute the same
    sequence_number. Without the row lock, both could read the same
    "current max" and both insert sequence_number=0, violating
    UniqueConstraint(chat_session_id, sequence_number). With the lock,
    the second caller blocks until the first commits, so both
    messages persist with distinct, correct sequence numbers.
    """
    import asyncio
    import os

    from sqlalchemy import select as sa_select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    document = await _make_document(db_session, "concurrency")
    session = await chat_session_service.create_session(db_session, document=document)
    session_id = session.id

    test_database_url = os.environ.get(
        "TEST_DATABASE_URL",
        settings.database_url.replace("/researchpilot", "/researchpilot_test"),
    )
    engine = create_async_engine(test_database_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _write_one_message(role: str, content: str):
        async with session_factory() as concurrent_session:
            result = await concurrent_session.execute(
                sa_select(ChatSession).where(ChatSession.id == session_id)
            )
            fetched_session = result.scalar_one()
            await chat_session_service._stage_message(
                concurrent_session, session=fetched_session, role=role, content=content
            )
            await concurrent_session.commit()

    await asyncio.gather(
        _write_one_message("user", "concurrent message A"),
        _write_one_message("assistant", "concurrent message B"),
    )
    await engine.dispose()

    messages = await chat_session_service.list_messages_for_session(db_session, session=session)
    assert len(messages) == 2
    assert {m.sequence_number for m in messages} == {0, 1}
