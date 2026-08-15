"""
Chat request/response schemas.

Single-Document Chat API milestone: the first HTTP-facing schemas for
the RAG pipeline. Mirrors DocumentResponse's deliberate narrowness —
the response contains exactly the public answer representation, never
retrieved chunks, distances, prompts, or provider metadata.

Chat Persistence milestone: adds ChatSessionResponse, ChatMessageResponse,
and SendMessageRequest for the new session/message endpoints.
ChatRequest/ChatResponse above are unchanged — the existing stateless
POST /documents/{document_id}/chat endpoint keeps its exact contract.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class ChatRequest(BaseModel):
    """
    The request body for POST /documents/{document_id}/chat.

    Deliberately contains only `question` — no top_k, model,
    temperature, conversation_id, session_id, history, or messages.
    This endpoint is a single stateless question, not a chat session.
    """

    question: str

    @field_validator("question")
    @classmethod
    def _reject_blank_question(cls, value: str) -> str:
        # Pydantic's own field validation, not a custom framework —
        # rejects missing (handled by the required-field default),
        # empty, and whitespace-only questions at the schema boundary,
        # before the request ever reaches the router/RAG service.
        if not value.strip():
            raise ValueError("question must not be empty or whitespace-only")
        return value


class ChatResponse(BaseModel):
    """
    The response body for POST /documents/{document_id}/chat.

    Deliberately contains only `answer` — never retrieved chunks,
    chunk IDs, cosine distances, embeddings, prompts, or any OpenAI
    provider metadata. Citations/sources are an explicitly deferred,
    separate future milestone, not part of this response shape.
    """

    answer: str


class ChatSessionResponse(BaseModel):
    """
    A single chat session's public representation — id and
    created_at only. Never includes document_id's owning user, or any
    internal detail beyond what the client needs to reference this
    session in subsequent requests.
    """

    id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageResponse(BaseModel):
    """
    A single persisted message — role, content, sequence_number, and
    created_at. Never includes chat_session_id (redundant — the
    client already has it from the URL) or any RAG-internal detail
    (retrieved chunks, prompts, provider metadata) — same "answer
    text only" discipline ChatResponse already establishes.
    """

    id: UUID
    role: str
    content: str
    sequence_number: int
    created_at: datetime

    model_config = {"from_attributes": True}


class SendMessageRequest(BaseModel):
    """
    The request body for POST
    /documents/{document_id}/chat/sessions/{session_id}/messages.

    Deliberately identical in shape to ChatRequest (just `question`,
    with the same blank-rejection validation) — kept as a separate
    class rather than reusing ChatRequest, since the two endpoints are
    conceptually distinct operations (stateless single question vs. a
    turn within a persisted session) that happen to currently share a
    request shape; they are free to diverge later without one
    accidentally constraining the other.
    """

    question: str

    @field_validator("question")
    @classmethod
    def _reject_blank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be empty or whitespace-only")
        return value
