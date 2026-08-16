"""
Tests for app.services.chunk_service.

Two groups: pure chunk_text() unit tests (no DB — mirrors
test_parse_service.py's style for a decoupled, file/DB-free
function), and DB-level tests of _replace_chunks() (mirrors
test_document_text_service.py's real-Postgres, real-Document/
real-DocumentText setup — no mocking, real FK constraints enforced).
"""
import pymupdf
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document_chunk import DocumentChunk
from app.models.document_text import DocumentText
from app.services import auth_service, chunk_service, document_service


# --- Pure chunk_text() tests ---


def test_chunk_text_empty_string_returns_no_chunks():
    assert chunk_service.chunk_text("") == []


def test_chunk_text_whitespace_only_returns_no_chunks():
    assert chunk_service.chunk_text("   \n\n   \n\n  ") == []


def test_chunk_text_short_paragraphs_combine_into_one_chunk():
    content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunk_service.chunk_text(content)
    assert chunks == [content]


def test_chunk_text_preserves_paragraph_order_across_multiple_chunks():
    # Four paragraphs sized so they don't all fit in one 1000-char
    # chunk: two ~600-char paragraphs guarantee a split (600+2+600 >
    # 1000), giving two chunks with clean, checkable boundaries.
    p1 = "Alpha. " * 90  # ~630 chars
    p2 = "Beta. " * 100  # ~600 chars
    content = f"{p1}\n\n{p2}"

    chunks = chunk_service.chunk_text(content)

    assert len(chunks) == 2
    assert chunks[0] == p1.strip()
    assert chunks[1] == p2.strip()


def test_chunk_text_combines_paragraphs_up_to_target_then_splits():
    # Three short paragraphs (300 chars each): first two combine
    # (300+2+300=602 <= 1000), the third pushes past target
    # (602+2+300=904... still <= 1000) — use four paragraphs instead
    # so the boundary is unambiguous and independent of exact math.
    paragraphs = ["X" * 300, "Y" * 300, "Z" * 300, "W" * 300]
    content = "\n\n".join(paragraphs)

    chunks = chunk_service.chunk_text(content)

    # 300+2+300+2+300 = 904 <= 1000 (three combine); adding a fourth
    # (904+2+300=1206) exceeds 1000, so it starts a new chunk.
    assert len(chunks) == 2
    assert chunks[0] == "\n\n".join(paragraphs[:3])
    assert chunks[1] == paragraphs[3]


def test_chunk_text_paragraph_between_target_and_max_stays_whole():
    # 1100 chars: over TARGET (1000) but under MAX (1200) — must be
    # kept as a single, unsplit chunk.
    paragraph = "A" * 1100
    chunks = chunk_service.chunk_text(paragraph)
    assert chunks == [paragraph]


def test_chunk_text_oversized_paragraph_is_hard_split_with_overlap():
    # 2500 chars, well over MAX (1200) — must be hard-split.
    paragraph = "B" * 2500
    chunks = chunk_service.chunk_text(paragraph)

    assert len(chunks) > 1
    # Every chunk must be at most TARGET (1000) characters.
    assert all(len(c) <= 1000 for c in chunks)
    # Reassembling with the known overlap (150) should reconstruct
    # the original run of "B"s losslessly in content (ignoring the
    # overlap duplication), i.e. every chunk is pure "B"s.
    assert all(set(c) == {"B"} for c in chunks)


def test_chunk_text_hard_split_snaps_to_whitespace_when_available():
    # A long paragraph made of real words so a whitespace boundary
    # exists near the natural TARGET cut point — the split should not
    # land mid-word.
    word = "word "
    paragraph = (word * 250).strip()  # 1249 chars, > MAX
    chunks = chunk_service.chunk_text(paragraph)

    assert len(chunks) > 1
    for chunk in chunks:
        # No chunk should end mid-word (i.e. every chunk, stripped,
        # is composed of whole "word" tokens).
        assert all(token == "word" for token in chunk.split())


def test_chunk_text_hard_split_falls_back_to_raw_cut_with_no_whitespace():
    # A single unbroken token (e.g. a long URL) longer than MAX, with
    # no whitespace anywhere for the snap-back to find.
    paragraph = "x" * 2000
    chunks = chunk_service.chunk_text(paragraph)

    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_text_chunk_index_assignment_is_caller_responsibility():
    # chunk_text() itself returns plain strings in order; chunk_index
    # is assigned by _replace_chunks(), tested separately below.
    chunks = chunk_service.chunk_text("One.\n\nTwo.\n\nThree.")
    assert isinstance(chunks, list)
    assert all(isinstance(c, str) for c in chunks)


# --- DB-level _replace_chunks() tests ---

_SAMPLE_EMBEDDING_DIM = 1536


def _fake_embeddings(count: int) -> list[list[float]]:
    """Deterministic, distinguishable fake embeddings for testing —
    never a real embedding, and no OpenAI call is ever made by these
    tests (_replace_chunks() takes embeddings as a plain argument, so
    it has no knowledge of or dependency on embedding_service at
    all)."""
    return [[float(i)] * _SAMPLE_EMBEDDING_DIM for i in range(count)]


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"chunksvc_{suffix}@example.com",
        username=f"chunksvc{suffix}",
        password="password123",
    )


def _make_pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


async def _make_document_text(db_session: AsyncSession, content: str) -> DocumentText:
    """
    A real DocumentText, FK'd to a real Document (created via the
    real create_document() service, same convention as
    test_document_text_service.py) — DocumentText.document_id has a
    genuine FK constraint, so a fabricated document_id would fail.
    """
    user = await _make_user(db_session, suffix=str(id(content))[-8:])
    document = await document_service.create_document(
        db_session,
        user_id=user.id,
        content=_make_pdf_bytes("placeholder"),
        original_filename="placeholder.pdf",
        content_type="application/pdf",
    )
    document_text = DocumentText(document_id=document.id, content=content)
    db_session.add(document_text)
    await db_session.flush()
    return document_text


async def test_replace_chunks_persists_expected_rows(db_session: AsyncSession):
    document_text = await _make_document_text(
        db_session, "Paragraph one.\n\nParagraph two."
    )
    chunk_texts = chunk_service.chunk_text(document_text.content)

    chunks = await chunk_service._replace_chunks(
        db_session,
        document_text=document_text,
        chunk_texts=chunk_texts,
        embeddings=_fake_embeddings(len(chunk_texts)),
    )
    await db_session.commit()

    assert len(chunks) == 1  # short paragraphs combine into one chunk
    assert chunks[0].chunk_index == 0
    assert chunks[0].document_text_id == document_text.id

    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == document_text.id)
    )
    persisted = result.scalars().all()
    assert len(persisted) == 1
    assert persisted[0].content == "Paragraph one.\n\nParagraph two."


async def test_replace_chunks_assigns_sequential_chunk_index(db_session: AsyncSession):
    p1 = "Alpha. " * 90
    p2 = "Beta. " * 100
    document_text = await _make_document_text(db_session, f"{p1}\n\n{p2}")
    chunk_texts = chunk_service.chunk_text(document_text.content)

    chunks = await chunk_service._replace_chunks(
        db_session,
        document_text=document_text,
        chunk_texts=chunk_texts,
        embeddings=_fake_embeddings(len(chunk_texts)),
    )
    await db_session.commit()

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


async def test_replace_chunks_each_chunk_receives_its_corresponding_embedding(
    db_session: AsyncSession,
):
    p1 = "Alpha. " * 90
    p2 = "Beta. " * 100
    document_text = await _make_document_text(db_session, f"{p1}\n\n{p2}")
    chunk_texts = chunk_service.chunk_text(document_text.content)
    embeddings = _fake_embeddings(len(chunk_texts))

    chunks = await chunk_service._replace_chunks(
        db_session,
        document_text=document_text,
        chunk_texts=chunk_texts,
        embeddings=embeddings,
    )
    await db_session.commit()

    # Each chunk's embedding matches the embedding at its own index —
    # not shuffled, not shared, not the wrong one paired up.
    for chunk, expected_embedding in zip(chunks, embeddings):
        assert list(chunk.embedding) == pytest.approx(expected_embedding)


async def test_replace_chunks_rejects_mismatched_chunk_and_embedding_counts(
    db_session: AsyncSession,
):
    document_text = await _make_document_text(db_session, "One paragraph only.")
    chunk_texts = chunk_service.chunk_text(document_text.content)

    with pytest.raises(ValueError):
        await chunk_service._replace_chunks(
            db_session,
            document_text=document_text,
            chunk_texts=chunk_texts,
            embeddings=_fake_embeddings(len(chunk_texts) + 1),  # deliberately mismatched
        )


async def test_replace_chunks_empty_text_persists_zero_rows(db_session: AsyncSession):
    document_text = await _make_document_text(db_session, "")
    chunk_texts = chunk_service.chunk_text(document_text.content)

    chunks = await chunk_service._replace_chunks(
        db_session,
        document_text=document_text,
        chunk_texts=chunk_texts,
        embeddings=_fake_embeddings(len(chunk_texts)),
    )
    await db_session.commit()

    assert chunks == []
    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == document_text.id)
    )
    assert result.scalars().all() == []


async def test_replace_chunks_reprocessing_removes_stale_rows_no_duplicates(
    db_session: AsyncSession,
):
    document_text = await _make_document_text(db_session, "Original short paragraph.")
    first_texts = chunk_service.chunk_text(document_text.content)

    first_chunks = await chunk_service._replace_chunks(
        db_session,
        document_text=document_text,
        chunk_texts=first_texts,
        embeddings=_fake_embeddings(len(first_texts)),
    )
    await db_session.commit()
    assert len(first_chunks) == 1

    # Simulate reprocessing with different (longer) content.
    document_text.content = "\n\n".join(["Paragraph."] * 3)
    await db_session.flush()
    second_texts = chunk_service.chunk_text(document_text.content)

    second_chunks = await chunk_service._replace_chunks(
        db_session,
        document_text=document_text,
        chunk_texts=second_texts,
        embeddings=_fake_embeddings(len(second_texts)),
    )
    await db_session.commit()

    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == document_text.id)
    )
    persisted = result.scalars().all()

    # No stale rows survive, no duplicates: exactly the new chunk set.
    assert len(persisted) == len(second_chunks)
    assert {c.id for c in persisted} == {c.id for c in second_chunks}
    assert first_chunks[0].id not in {c.id for c in persisted}


async def test_replace_chunks_does_not_commit_independently(db_session: AsyncSession):
    """
    _replace_chunks() must only flush, never commit — the caller
    (document_processing_service) owns the transaction boundary. If
    this function committed on its own, rolling back afterward
    wouldn't undo the insert; this test proves rollback still removes
    everything, confirming no independent commit happened.
    """
    document_text = await _make_document_text(db_session, "Rollback check.")
    chunk_texts = chunk_service.chunk_text(document_text.content)

    await chunk_service._replace_chunks(
        db_session,
        document_text=document_text,
        chunk_texts=chunk_texts,
        embeddings=_fake_embeddings(len(chunk_texts)),
    )
    # Deliberately roll back instead of committing.
    await db_session.rollback()

    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == document_text.id)
    )
    assert result.scalars().all() == []
