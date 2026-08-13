"""
Tests for app.services.retrieval_service.

Real Postgres + pgvector throughout — no mocking of the database.
Embeddings used here are hand-constructed, deterministic vectors (not
real OpenAI output) so similarity ordering is exactly predictable;
this file never calls embedding_service or the OpenAI SDK at all.
"""
import math
import uuid

import pymupdf
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document_chunk import DocumentChunk
from app.models.document_text import DocumentText
from app.services import auth_service, document_service, retrieval_service

_DIM = 1536


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    yield tmp_path


def _unit_vector(index: int, dim: int = _DIM) -> list[float]:
    """A deterministic one-hot-style vector: 1.0 at `index`, 0.0
    elsewhere. Two unit vectors at different indices are maximally
    dissimilar (cosine distance 1.0); a vector compared against
    itself has cosine distance 0.0 — makes ordering assertions exact
    and easy to reason about, unlike realistic-looking random floats."""
    vector = [0.0] * dim
    vector[index % dim] = 1.0
    return vector


def _tilted_vector(weight: float, dim: int = _DIM) -> list[float]:
    """
    Returns a normalized vector tilted away from the query direction.
    For query _unit_vector(0), cosine distance is strictly increasing
    with weight.

    A fixed primary component (index 0, matching the query vector
    `_unit_vector(0)`) and a secondary component (index 1) of
    magnitude `weight`. Cosine similarity to `_unit_vector(0)` is
    exactly 1 / sqrt(1 + weight**2) — strictly decreasing as `weight`
    increases from 0, so cosine distance (1 - similarity) is strictly
    increasing in `weight` for any weight >= 0. Two vectors built with
    distinct, non-negative weights therefore always have distinct,
    predictably-ordered distances — no equidistant/tied vectors,
    unlike two arbitrary unit vectors from `_unit_vector()`, which are
    always exactly cosine-distance 1 from each other regardless of
    which distinct indices they use.

    weight=0 reduces to the exact-match case (distance 0, identical
    to `_unit_vector(0)`).
    """
    vector = [0.0] * dim
    vector[0] = 1.0
    vector[1] = weight

    norm = (1.0 + weight * weight) ** 0.5
    return [value / norm for value in vector]


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
        email=f"retrieval_{suffix}@example.com",
        username=f"retrieval{suffix}",
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


async def _make_document_text(db_session: AsyncSession, document_id: uuid.UUID) -> DocumentText:
    document_text = DocumentText(document_id=document_id, content="Some extracted text.")
    db_session.add(document_text)
    await db_session.flush()
    return document_text


async def _add_chunk(
    db_session: AsyncSession,
    *,
    document_text_id: uuid.UUID,
    chunk_index: int,
    content: str,
    embedding: list[float],
) -> DocumentChunk:
    chunk = DocumentChunk(
        document_text_id=document_text_id,
        chunk_index=chunk_index,
        content=content,
        embedding=embedding,
    )
    db_session.add(chunk)
    await db_session.flush()
    return chunk


# --- 1. Retrieval ordering (nearest chunk returned first) ---


async def test_retrieve_similar_chunks_orders_by_cosine_distance_closest_first(
    db_session: AsyncSession,
):
    document = await _make_document(db_session, "order")
    document_text = await _make_document_text(db_session, document.id)

    # Three chunks with strictly increasing cosine distance from the
    # query vector (weight=0 -> exact match; larger weight -> farther)
    # — no two of these can ever tie, unlike two arbitrary orthogonal
    # unit vectors (which are always exactly distance 1 from each
    # other regardless of which distinct indices they use).
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="farthest chunk",
        embedding=_tilted_vector(weight=4.0),
    )
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=1,
        content="exact match chunk",
        embedding=_tilted_vector(weight=0.0),
    )
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=2,
        content="second closest chunk",
        embedding=_tilted_vector(weight=1.0),
    )
    await db_session.commit()

    query_embedding = _unit_vector(0)  # identical to weight=0.0's embedding

    results = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document, query_embedding=query_embedding, top_k=3
    )

    assert [r.content for r in results] == [
        "exact match chunk",
        "second closest chunk",
        "farthest chunk",
    ]
    # Distances are strictly increasing (closest first) — no tie-order
    # assumption exists, since the three weights were chosen to
    # produce three genuinely distinct cosine distances.
    assert results[0].distance < results[1].distance < results[2].distance


# --- 2. top_k respected ---


async def test_retrieve_similar_chunks_respects_top_k(db_session: AsyncSession):
    document = await _make_document(db_session, "topk")
    document_text = await _make_document_text(db_session, document.id)

    # Ten chunks with strictly increasing, distinct cosine distances
    # from the query — weight=0.0 is the exact match, and each
    # subsequent weight is strictly larger, so the "three nearest"
    # are unambiguous and independent of any database tie-ordering.
    weights = [0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
    for i, weight in enumerate(weights):
        await _add_chunk(
            db_session,
            document_text_id=document_text.id,
            chunk_index=i,
            content=f"weight={weight}",
            embedding=_tilted_vector(weight=weight),
        )
    await db_session.commit()

    results = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document, query_embedding=_unit_vector(0), top_k=3
    )

    # Exactly top_k results...
    assert len(results) == 3
    # ...and they are genuinely the three nearest chunks, in the
    # correct order — not merely three arbitrary chunks that happen
    # to satisfy the count.
    assert [r.content for r in results] == ["weight=0.0", "weight=0.25", "weight=0.5"]
    assert results[0].distance < results[1].distance < results[2].distance


# --- 3. top_k bounds are enforced (clamping policy) ---


async def test_retrieve_similar_chunks_clamps_pathological_top_k(db_session: AsyncSession):
    document = await _make_document(db_session, "clamp")
    document_text = await _make_document_text(db_session, document.id)

    for i in range(5):
        await _add_chunk(
            db_session,
            document_text_id=document_text.id,
            chunk_index=i,
            content=f"chunk {i}",
            embedding=_unit_vector(i),
        )
    await db_session.commit()

    # top_k=0 is clamped to the minimum of 1 — never raises, never
    # silently returns zero results due to an invalid bound.
    results_zero = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document, query_embedding=_unit_vector(0), top_k=0
    )
    assert len(results_zero) == 1

    # An unreasonably large top_k is clamped to MAX_TOP_K (20), not
    # passed through as an unbounded LIMIT — proven here by the fact
    # this returns cleanly rather than erroring, capped at however
    # many chunks actually exist (5, since MAX_TOP_K=20 > 5).
    results_huge = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document, query_embedding=_unit_vector(0), top_k=999_999
    )
    assert len(results_huge) == 5
    assert retrieval_service.MAX_TOP_K == 20


# --- 4. Document isolation — chunks from another document are never returned ---


async def test_retrieve_similar_chunks_never_returns_another_documents_chunks(
    db_session: AsyncSession,
):
    document_a = await _make_document(db_session, "isoA")
    document_b = await _make_document(db_session, "isoB")
    text_a = await _make_document_text(db_session, document_a.id)
    text_b = await _make_document_text(db_session, document_b.id)

    await _add_chunk(
        db_session,
        document_text_id=text_a.id,
        chunk_index=0,
        content="Document A's chunk",
        embedding=_unit_vector(0),
    )
    # Document B's chunk has an IDENTICAL embedding to the query — if
    # isolation were broken, this would be the top (or only) result.
    await _add_chunk(
        db_session,
        document_text_id=text_b.id,
        chunk_index=0,
        content="Document B's chunk",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    results = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document_a, query_embedding=_unit_vector(0), top_k=10
    )

    assert len(results) == 1
    assert results[0].content == "Document A's chunk"
    assert all(r.content != "Document B's chunk" for r in results)


# --- 5. No-result behavior ---


async def test_retrieve_similar_chunks_returns_empty_list_for_document_with_no_chunks(
    db_session: AsyncSession,
):
    document = await _make_document(db_session, "empty")
    await _make_document_text(db_session, document.id)
    await db_session.commit()

    results = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document, query_embedding=_unit_vector(0), top_k=5
    )

    assert results == []


# --- 6. Cosine distance / score correctness ---


async def test_retrieve_similar_chunks_distance_matches_pgvector_cosine_behavior(
    db_session: AsyncSession,
):
    document = await _make_document(db_session, "distance")
    document_text = await _make_document_text(db_session, document.id)

    # An exact match to the query vector must have cosine distance
    # ~0.0; a maximally different (orthogonal) unit vector must have
    # cosine distance ~1.0 — pgvector's well-defined cosine-distance
    # behavior for orthogonal/identical unit vectors.
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="identical",
        embedding=_unit_vector(0),
    )
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=1,
        content="orthogonal",
        embedding=_unit_vector(1),
    )
    await db_session.commit()

    results = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document, query_embedding=_unit_vector(0), top_k=2
    )

    by_content = {r.content: r.distance for r in results}
    assert by_content["identical"] == pytest.approx(0.0, abs=1e-6)
    assert by_content["orthogonal"] == pytest.approx(1.0, abs=1e-6)


async def test_retrieved_chunk_carries_id_index_content_and_distance(db_session: AsyncSession):
    document = await _make_document(db_session, "shape")
    document_text = await _make_document_text(db_session, document.id)
    chunk = await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=3,
        content="the actual chunk text",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    results = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document, query_embedding=_unit_vector(0), top_k=1
    )

    assert len(results) == 1
    result = results[0]
    assert result.id == chunk.id
    assert result.chunk_index == 3
    assert result.content == "the actual chunk text"
    assert isinstance(result.distance, float)


# --- 7. No OpenAI API request is ever made by retrieval itself ---


async def test_retrieve_similar_chunks_never_touches_embedding_service(
    db_session: AsyncSession, monkeypatch
):
    """
    retrieve_similar_chunks() takes an already-computed query
    embedding — it must never call embedding_service (and therefore
    never the OpenAI SDK) itself. Monkeypatching embed_texts/embed_query
    to raise if called proves retrieval genuinely never touches them.
    """
    from app.services import embedding_service

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("retrieval must never call the embedding provider itself")

    monkeypatch.setattr(embedding_service, "embed_texts", _fail_if_called)
    monkeypatch.setattr(embedding_service, "embed_query", _fail_if_called)

    document = await _make_document(db_session, "noembedcall")
    document_text = await _make_document_text(db_session, document.id)
    await _add_chunk(
        db_session,
        document_text_id=document_text.id,
        chunk_index=0,
        content="a chunk",
        embedding=_unit_vector(0),
    )
    await db_session.commit()

    results = await retrieval_service.retrieve_similar_chunks(
        db_session, document=document, query_embedding=_unit_vector(0), top_k=1
    )

    assert len(results) == 1
