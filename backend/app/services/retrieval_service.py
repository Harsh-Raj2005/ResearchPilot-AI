"""
Retrieval service.

Vector Retrieval milestone: the first real consumer of
document_chunks.embedding — everything before this milestone wrote
embeddings, nothing read them back for similarity search.

Deliberately narrow responsibility: given an already-computed query
embedding and an already-authorized Document, return that document's
chunks ordered by similarity. This module does NOT call OpenAI
directly (embedding_service.py owns that), does NOT perform HTTP or
authorization (the caller is responsible for obtaining an
ownership-verified Document via document_service.get_document_for_user
first, exactly mirroring the existing pattern
document_text_service.parse_and_store_document_text() already
establishes: accept an already-authorized Document, perform zero
ownership checks here), does NOT implement RAG, and does NOT construct
prompts or generate answers — those are explicitly out of scope for
this milestone.

No new public HTTP endpoint exists yet — this is an internal service
primitive, ready for a future RAG/chat layer to call, per the
project's standing "no speculative public API" principle. If/when
that future layer needs an HTTP-reachable retrieval endpoint, that's
a separate, later design decision, not assumed here.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_text import DocumentText

# Phase 1 is single-document, interactive retrieval — a handful of
# results is what a future RAG layer would actually consume. MAX_TOP_K
# exists purely to prevent a pathological caller-supplied value (e.g.
# top_k=100000) from turning a bounded query into an unbounded one;
# it is not a tuned performance parameter.
DEFAULT_TOP_K = 5
MAX_TOP_K = 20


@dataclass(frozen=True)
class RetrievedChunk:
    """
    The minimum a future RAG/chat caller needs per result. Deliberately
    a small, focused dataclass rather than a Pydantic schema — nothing
    here crosses an HTTP boundary (schemas/ is reserved for that), and
    a plain dataclass is the smallest structure that fits, matching
    the project's "no DTO hierarchy for its own sake" principle.

    `distance` is pgvector's cosine distance (0 = identical direction,
    2 = opposite direction; smaller is more similar) — the raw value
    from the `<=>` operator, not a normalized "similarity score", so a
    future caller gets the real metric rather than an invented one.
    """

    id: uuid.UUID
    chunk_index: int
    content: str
    distance: float


async def retrieve_similar_chunks(
    db: AsyncSession,
    *,
    document: Document,
    query_embedding: list[float],
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievedChunk]:
    """
    Returns `document`'s chunks ordered by cosine distance to
    `query_embedding`, closest first, bounded to `top_k` results.

    `document` must already be ownership-verified by the caller (via
    document_service.get_document_for_user() or equivalent) — this
    function performs no authorization of its own, by design (see
    this module's own docstring). Scoping is enforced by joining
    through DocumentText to DocumentChunk and filtering on
    `DocumentText.document_id == document.id`, since DocumentChunk has
    no direct document_id column (chunks belong to DocumentText, not
    Document — see DocumentChunk's own model docstring); this join is
    the only way to reach "this document's chunks" and is exactly the
    same relationship chain every other part of this codebase already
    uses.

    `top_k` is clamped to [1, MAX_TOP_K] rather than trusted as-is —
    a caller-supplied value of 0, a negative number, or an
    unreasonably large number is silently corrected to the nearest
    valid bound rather than raising, since this is an internal
    primitive with no request-validation boundary of its own yet.

    A document with zero chunks (or zero matching this document)
    returns an empty list — not an error. This is a normal, expected
    outcome (e.g. an unprocessed document, or one whose extracted text
    was empty), matching the same "empty is a valid result, not a
    failure" treatment already established for DocumentChunk itself.
    """
    top_k = max(1, min(top_k, MAX_TOP_K))

    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
    result = await db.execute(
        select(DocumentChunk, distance.label("distance"))
        .join(DocumentText, DocumentChunk.document_text_id == DocumentText.id)
        .where(DocumentText.document_id == document.id)
        .order_by(distance)
        .limit(top_k)
    )

    return [
        RetrievedChunk(
            id=chunk.id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            distance=distance_value,
        )
        for chunk, distance_value in result.all()
    ]
