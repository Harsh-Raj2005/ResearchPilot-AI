"""
Chat request/response schemas.

Single-Document Chat API milestone: the first HTTP-facing schemas for
the RAG pipeline. Mirrors DocumentResponse's deliberate narrowness —
the response contains exactly the public answer representation, never
retrieved chunks, distances, prompts, or provider metadata.
"""
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
