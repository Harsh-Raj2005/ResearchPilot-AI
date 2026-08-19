"""
Documents router.

Task 3B Checkpoint 3 scope: upload. Document Management CRUD
Checkpoint 1: authenticated listing. Checkpoint 2: authenticated
detail. Checkpoint 3: authenticated download. Checkpoint 4: authenticated
delete — completed the full CRUD surface. Document Text Extraction
Checkpoint 5 (this checkpoint): authenticated processing — triggers
text extraction for an already-uploaded document on demand. This is
`get_current_user`'s consumer for all six routes — every route here
requires a valid bearer token.

Deliberately thin: routes validate their own path/query params
(document_id's type, pagination bounds), enforce the size cap on
upload (the one piece of validation that genuinely belongs at this
layer — see Section 11 #24), and delegate everything else to
document_service / document_text_service. No business logic and no
direct DB query or filesystem access lives here.

Upload does NOT call document_text_service — processing is an
explicit, separate operation the caller triggers via
POST /{document_id}/process, not an automatic side effect of upload.
See PROJECT_CONTEXT.md for the design rationale (Checkpoint 5).
"""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import (
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    ChatSessionResponse,
    SendMessageRequest,
)
from app.schemas.document import DocumentResponse
from app.services import (
    chat_session_service,
    document_processing_service,
    document_service,
    embedding_service,
    llm_service,
    parse_service,
    rag_service,
    storage_service,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    content = await file.read()

    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.max_upload_size_mb}MB",
        )

    try:
        document = await document_service.create_document(
            db,
            user_id=current_user.id,
            content=content,
            original_filename=file.filename or "",
            content_type=file.content_type or "application/octet-stream",
        )
    except storage_service.UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except storage_service.StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    return DocumentResponse.model_validate(document)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    skip: int = Query(0, ge=0, description="Number of documents to skip"),
    limit: int = Query(20, ge=1, le=100, description="Maximum documents to return"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    """
    List the authenticated user's documents, newest first.

    Bounded pagination via skip/limit query params — plain, no
    pagination framework, matching how the rest of this project favors
    the simplest option that works. limit is capped at 100 so a caller
    can't request an unreasonably large result set in one call.
    """
    documents = await document_service.list_documents_for_user(
        db, user_id=current_user.id, skip=skip, limit=limit
    )
    return [DocumentResponse.model_validate(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """
    Return metadata for a single document owned by the authenticated
    user.

    A nonexistent document and another user's document return the
    identical 404 — the service's combined WHERE clause (id AND
    user_id) means there's no code path where this router ever learns
    which case it was, so there's nothing to accidentally leak.
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    return DocumentResponse.model_validate(document)


@router.get("/{document_id}/file")
async def download_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Return the actual stored file for a document owned by the
    authenticated user.

    Same indistinguishable-404 behavior as the detail endpoint for
    "doesn't exist" vs. "belongs to someone else" — both flow through
    the same get_document_for_user() ownership check inside
    document_service.get_document_file_for_user(). A document that
    exists and is owned by the caller, but whose object is missing
    from storage, is a different situation (a server-side data
    integrity problem, not a client error) and gets a distinct 500 —
    without ever revealing the storage object key or bucket in the
    response.

    Deployment milestone: builds the response from raw bytes via a
    plain Response, not FastAPI's FileResponse — FileResponse streams
    from a real local filesystem path, which no longer exists now
    that storage is Cloudflare R2 (see storage_service.py's own
    docstring for why get_file_bytes() and get_file_path() are two
    distinct functions). Content-Disposition is set explicitly here
    to preserve the exact same "attachment; filename=..." contract
    FileResponse's own filename= parameter used to generate
    automatically.

    No response_model here: the body is the file itself, not a JSON
    DocumentResponse — the two are mutually exclusive for this route.
    """
    try:
        result = await document_service.get_document_file_for_user(
            db, document_id=document_id, user_id=current_user.id
        )
    except storage_service.StoredFileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored file for this document could not be found.",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    document, file_bytes = result
    return Response(
        content=file_bytes,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{document.original_filename}"'
        },
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a document (stored file + database row) owned by the
    authenticated user.

    204 No Content on success — no existing project convention uses a
    custom JSON success body for an action endpoint, and 204 is the
    conventional REST response for a delete with nothing further to
    return. Same indistinguishable-404 behavior as detail/download for
    "doesn't exist" vs. "belongs to someone else" — both flow through
    the same get_document_for_user() check inside
    document_service.delete_document_for_user(). A genuine filesystem
    failure while deleting the file (StorageError — distinct from
    "already missing", which delete_file() treats as success) is a
    500, and deliberately leaves the Document row untouched rather
    than deleting it — see document_service.delete_document_for_user's
    docstring for the full data-integrity reasoning.

    Note: a second DELETE of the same document_id returns 404, not
    another 204 — once deleted, the document genuinely no longer
    exists for this user, so "not found" is the accurate response,
    not a false "deleted again" success.
    """
    try:
        deleted = await document_service.delete_document_for_user(
            db, document_id=document_id, user_id=current_user.id
        )
    except storage_service.StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete the stored file for this document.",
        ) from exc

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )


@router.post("/{document_id}/process", response_model=DocumentResponse)
async def process_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """
    Parse a document owned by the authenticated user and persist the
    extracted text — Document Text Extraction Checkpoint 5's explicit
    processing operation.

    Deliberately a separate, on-demand endpoint rather than an
    automatic step of upload (see PROJECT_CONTEXT.md for the full
    Checkpoint 5 design rationale): upload's existing behavior is
    completely unchanged by this route's addition.

    Same indistinguishable-404 behavior as every other {document_id}
    route for "doesn't exist" vs. "belongs to someone else" — both
    flow through the existing get_document_for_user() ownership
    check, reused as-is rather than duplicated here.

    Returns the existing DocumentResponse (200) on success — the same
    Document, re-serialized. Deliberately never returns DocumentText
    or its `content`: extracted text remains internal processing
    state, not something this (or any) endpoint exposes (see
    PROJECT_CONTEXT.md Section 11 #54).

    Calling this endpoint again for an already-processed document is
    how reprocessing works — DocumentText and DocumentChunk rows are
    both replaced (via document_processing_service.process_document(),
    which upserts DocumentText and deletes+recreates DocumentChunk
    rows) inside one transaction, so no separate reprocess mechanism
    or duplicate-prevention logic is needed here, and a document never
    ends up with new text paired with stale chunks.

    Error mapping, mirroring this file's existing exception-translation
    style (see upload/download above):
    - UnsupportedFormatError -> 422 (the document's content can't be
      processed by this parser, e.g. a non-PDF upload)
    - ParseError -> 422 (the file exists but its content is invalid
      or corrupted, so extraction can't produce a result)
    - StoredFileNotFoundError -> 500 (a server-side data-integrity
      problem — the Document row is valid and owned by the caller,
      but its file is missing from disk — distinct from a client
      error, same as download's identical handling)
    - EmbeddingProviderError -> 502 (Document Chunks -> Embeddings
      milestone: an external dependency — the embedding provider —
      failed. Distinct from both 422 (the client's document content)
      and 500 (our own storage integrity): this is specifically
      "an upstream service we depend on didn't respond correctly."
      The client-facing message is generic — no raw provider
      exception text, response body, or API key ever reaches the
      response.)
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    try:
        await document_processing_service.process_document(db, document=document)
    except (parse_service.UnsupportedFormatError, parse_service.ParseError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except storage_service.StoredFileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored file for this document could not be found.",
        ) from exc
    except embedding_service.EmbeddingProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Embedding generation failed. Please try again.",
        ) from exc

    return DocumentResponse.model_validate(document)


@router.post("/{document_id}/chat", response_model=ChatResponse)
async def chat_with_document(
    document_id: uuid.UUID,
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """
    Ask one question about one document owned by the authenticated
    user — Single-Document Chat API milestone. Stateless: no
    conversation history, no session, no memory. Exposes the
    existing internal rag_service.answer_question() pipeline
    (RAG Foundation) through a thin authenticated HTTP endpoint.

    Same indistinguishable-404 behavior as every other {document_id}
    route: both a nonexistent document and one owned by another user
    return an identical 404, via the existing
    get_document_for_user() ownership check, reused as-is rather
    than duplicated here. rag_service.answer_question() is never
    called when ownership resolution fails.

    The router treats rag_service as a black box: it does not
    generate embeddings, query pgvector, retrieve chunks, construct
    prompts, or call the LLM directly. All of that already belongs
    to the RAG/embedding/retrieval/LLM service layers, unmodified
    by this endpoint.

    The response contains only the answer text — never retrieved
    chunks, chunk IDs, cosine distances, embeddings, prompts, or any
    OpenAI provider metadata. Citations/sources are an explicitly
    deferred, separate future milestone.

    Error mapping, mirroring this file's existing exception-translation
    style (see /process above):
    - LLMProviderError -> 502 (an upstream dependency — the LLM
      provider — failed. Same category and treatment as
      EmbeddingProviderError's existing 502 mapping above: a generic
      client-facing message only, no raw provider exception text,
      response body, or API key ever reaches the response.)
    - EmbeddingProviderError -> 502, same treatment. rag_service.
      answer_question() calls embedding_service.embed_query() before
      retrieval; that failure was previously uncaught here and fell
      through to a raw 500 — this mirrors the existing /process
      mapping instead. Phase 1 hardening fix, not a new architecture.
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    try:
        answer = await rag_service.answer_question(
            db, document=document, question=request.question
        )
    except llm_service.LLMProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer generation failed. Please try again.",
        ) from exc
    except embedding_service.EmbeddingProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer generation failed. Please try again.",
        ) from exc

    return ChatResponse(answer=answer)


@router.post(
    "/{document_id}/chat/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_session(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatSessionResponse:
    """
    Create a new, empty chat session for a document owned by the
    authenticated user — Chat Persistence milestone.

    Same indistinguishable-404 behavior as every other
    {document_id} route, via the existing get_document_for_user()
    ownership check, reused as-is.
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    session = await chat_session_service.create_session(db, document=document)
    return ChatSessionResponse.model_validate(session)


@router.get(
    "/{document_id}/chat/sessions",
    response_model=list[ChatSessionResponse],
)
async def list_chat_sessions(
    document_id: uuid.UUID,
    skip: int = Query(0, ge=0, description="Number of sessions to skip"),
    limit: int = Query(20, ge=1, le=100, description="Maximum sessions to return"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatSessionResponse]:
    """
    List a document's chat sessions, newest first — Chat
    Persistence milestone. Same plain skip/limit pagination
    convention already established by GET /documents.
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    sessions = await chat_session_service.list_sessions_for_document(
        db, document=document, skip=skip, limit=limit
    )
    return [ChatSessionResponse.model_validate(session) for session in sessions]


@router.get(
    "/{document_id}/chat/sessions/{session_id}/messages",
    response_model=list[ChatMessageResponse],
)
async def list_chat_messages(
    document_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatMessageResponse]:
    """
    Return a chat session's full transcript, in conversation order
    — Chat Persistence milestone.

    Two nested ownership checks, both collapsing to the same 404:
    first that document_id exists and is owned by the caller (the
    existing get_document_for_user() check), then that session_id
    actually belongs to that document (chat_session_service.
    get_session_for_document()). A session_id alone is
    guessable/enumerable, so every session-level route is scoped
    through its parent document, not just through session_id.
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    session = await chat_session_service.get_session_for_document(
        db, document=document, session_id=session_id
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        )

    messages = await chat_session_service.list_messages_for_session(db, session=session)
    return [ChatMessageResponse.model_validate(message) for message in messages]


@router.post(
    "/{document_id}/chat/sessions/{session_id}/messages",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_chat_message(
    document_id: uuid.UUID,
    session_id: uuid.UUID,
    request: SendMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageResponse:
    """
    Send a message within a persisted chat session — Chat
    Persistence milestone. Persists the user's message, calls
    rag_service.answer_question_with_history() with the session's
    prior transcript, persists the assistant's reply, and returns
    the assistant's newly-created ChatMessageResponse (the user's
    own message is not echoed back — the client already has its
    content from the request it just sent).

    Same nested-ownership pattern as list_chat_messages() above:
    document ownership, then session-belongs-to-document, both
    collapsing to an identical 404.

    The user's message and the assistant's reply are persisted
    atomically: both are staged (flushed, not committed) and a
    single db.commit() only happens after RAG has actually
    succeeded. If the RAG/LLM call fails, nothing commits at all —
    the staged user message is rolled back along with everything
    else, via get_db's existing implicit rollback-on-exception (the
    same guarantee this project has relied on since the Document
    Chunks -> Embeddings milestone). There is never a committed
    state with a user question but no assistant reply, and never a
    fabricated assistant reply persisted on its own.

    Error mapping identical to POST /chat above: LLMProviderError
    -> 502, generic message, no raw provider text leaked.
    EmbeddingProviderError -> 502 as well (Phase 1 hardening fix —
    answer_question_with_history() calls embed_query() before
    retrieval; that failure was previously uncaught here). Atomicity
    is unaffected: the staged user message is rolled back the same
    way it already is for a LLMProviderError failure.
    """
    document = await document_service.get_document_for_user(
        db, document_id=document_id, user_id=current_user.id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    session = await chat_session_service.get_session_for_document(
        db, document=document, session_id=session_id
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        )

    prior_messages = await chat_session_service.list_messages_for_session(db, session=session)
    history = [{"role": message.role, "content": message.content} for message in prior_messages]

    # Stage (flush, not commit) the user message before calling RAG.
    # If the RAG/LLM call below fails, nothing has been committed yet
    # -- FastAPI's get_db dependency rolls back the whole session on
    # the propagating exception, so the staged user message never
    # becomes durably visible either. This mirrors the exact
    # "stage everything, commit once at the end" pattern
    # document_processing_service.process_document() already
    # established for text+chunk+embedding persistence -- the user
    # message and the assistant reply become durable together, or
    # not at all; there is no possible committed state with a user
    # question but no assistant reply, and no possible committed
    # state with a fabricated assistant reply and no user question.
    await chat_session_service._stage_message(
        db, session=session, role="user", content=request.question
    )

    try:
        answer = await rag_service.answer_question_with_history(
            db, document=document, question=request.question, history=history
        )
    except llm_service.LLMProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer generation failed. Please try again.",
        ) from exc
    except embedding_service.EmbeddingProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer generation failed. Please try again.",
        ) from exc

    assistant_message = await chat_session_service._stage_message(
        db, session=session, role="assistant", content=answer
    )
    await db.commit()
    await db.refresh(assistant_message)
    return ChatMessageResponse.model_validate(assistant_message)
