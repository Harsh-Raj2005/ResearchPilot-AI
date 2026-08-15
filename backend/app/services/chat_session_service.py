"""
Chat session service.

Chat Persistence milestone: data-access layer for ChatSession and
ChatMessage, mirroring document_service.py's role for Document.
Routers call this; it never touches HTTP directly (no HTTPException
here — app/api/documents.py translates domain outcomes to status
codes, same convention as every other service in this codebase).

Deliberately does NOT duplicate document_service's ownership check —
get_session_for_document() accepts an already-authorized Document
(the caller obtains it via document_service.get_document_for_user()
first, exactly the pattern already established by
document_text_service, retrieval_service, and rag_service) and adds
only the second, session-scoped check this milestone introduces:
confirming the session actually belongs to that document. A session
ID alone is guessable/enumerable, so every session-level operation is
scoped through its parent document, not just through session_id.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.document import Document


async def create_session(db: AsyncSession, *, document: Document) -> ChatSession:
    """
    Creates a new, empty ChatSession for `document`.

    `document` must already be ownership-verified by the caller — see
    this module's own docstring. Commits internally, matching every
    other mutating function in document_service.py.
    """
    session = ChatSession(document_id=document.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_sessions_for_document(
    db: AsyncSession, *, document: Document, skip: int = 0, limit: int = 20
) -> list[ChatSession]:
    """
    Returns `document`'s chat sessions, newest first, bounded by
    skip/limit — same plain pagination convention already established
    by document_service.list_documents_for_user() (no pagination
    framework; bounds validated at the API boundary, this function
    trusts its caller).
    """
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.document_id == document.id)
        .order_by(ChatSession.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_session_for_document(
    db: AsyncSession, *, document: Document, session_id: uuid.UUID
) -> ChatSession | None:
    """
    Returns the ChatSession if it exists AND belongs to `document`,
    otherwise None — both conditions in the same WHERE clause,
    deliberately not "fetch by id, then check document_id in Python",
    mirroring document_service.get_document_for_user()'s identical
    reasoning: no code path ever loads another document's session
    into memory, and a wrong-document request is structurally
    indistinguishable from a nonexistent session_id. The router turns
    None into a single, uniform 404 either way.
    """
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.document_id == document.id
        )
    )
    return result.scalar_one_or_none()


async def list_messages_for_session(
    db: AsyncSession, *, session: ChatSession
) -> list[ChatMessage]:
    """
    Returns all of `session`'s messages, in conversation order
    (ascending sequence_number — see ChatMessage's own docstring for
    why sequence_number, not created_at, is the ordering key).
    """
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.chat_session_id == session.id)
        .order_by(ChatMessage.sequence_number.asc())
    )
    return list(result.scalars().all())


async def append_message(
    db: AsyncSession, *, session: ChatSession, role: str, content: str
) -> ChatMessage:
    """
    Appends one message to `session`, assigning the next
    sequence_number for this session, and commits.

    Standalone, self-committing entry point — for any caller that
    wants to append exactly one message on its own. The send-message
    HTTP route does NOT use this function: persisting a user message
    and an assistant reply as one atomic operation (see this module's
    _stage_message()) requires the two writes and the RAG call in
    between to share one transaction, not two independent commits.
    """
    message = await _stage_message(db, session=session, role=role, content=content)
    await db.commit()
    await db.refresh(message)
    return message


async def _stage_message(
    db: AsyncSession, *, session: ChatSession, role: str, content: str
) -> ChatMessage:
    """
    Assigns the next sequence_number for `session` and adds the new
    ChatMessage row — flush only, no commit.

    Exists so a caller that needs to persist a user message and an
    assistant reply as one atomic unit (with a RAG call in between)
    can stage both writes and issue a single commit only once RAG has
    actually succeeded — the exact same "stage everything, commit
    once at the end" pattern already established by
    document_processing_service.process_document() for text+chunk+
    embedding persistence. If the RAG call raises after the user
    message has been staged here, nothing has been committed —
    FastAPI's get_db dependency rolls back the whole session on the
    propagating exception (AsyncSession.close() -> implicit rollback,
    the same guarantee this project has relied on since the Document
    Chunks -> Embeddings milestone), so the staged user message never
    becomes durably visible either.

    Deliberately a single, simple "read max, then insert" for
    sequence_number rather than a database sequence/identity column —
    this project has no other per-parent sequence column precedent to
    match. Sequence allocation is serialized per session via
    `SELECT ... FOR UPDATE` on the ChatSession row itself before
    computing the next value: any second concurrent request for the
    same session blocks on that row lock until the first request's
    transaction commits or rolls back, so two concurrent callers can
    never compute the same next sequence_number for the same session.
    The database's own UniqueConstraint(chat_session_id,
    sequence_number) remains the final guard regardless.
    """
    await db.execute(
        select(ChatSession.id).where(ChatSession.id == session.id).with_for_update()
    )

    result = await db.execute(
        select(ChatMessage.sequence_number)
        .where(ChatMessage.chat_session_id == session.id)
        .order_by(ChatMessage.sequence_number.desc())
        .limit(1)
    )
    current_max = result.scalar_one_or_none()
    next_sequence_number = 0 if current_max is None else current_max + 1

    message = ChatMessage(
        chat_session_id=session.id,
        role=role,
        content=content,
        sequence_number=next_sequence_number,
    )
    db.add(message)
    await db.flush()
    return message
