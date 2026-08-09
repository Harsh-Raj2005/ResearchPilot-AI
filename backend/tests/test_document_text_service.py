"""
Tests for app.services.document_text_service.

Document Text Extraction Checkpoint 4 scope: the parse -> persist
integration. Uses document_service.create_document() for test setup
rather than hand-constructing Document rows — this exercises the real
storage path (a real file actually written to the isolated upload
dir), so document.storage_path is exactly what a real upload would
produce, not a fabricated value. Real PyMuPDF-generated PDF bytes
throughout, same style as test_parse_service.py; no mocking of
parse_service or storage_service.
"""
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document import Document
from app.models.document_text import DocumentText
from app.services import (
    auth_service,
    document_service,
    document_text_service,
    parse_service,
    storage_service,
)


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    """Same isolation pattern as test_documents_api.py / test_storage_service.py."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    yield tmp_path


def _make_pdf_bytes(pages_text: list[str]) -> bytes:
    """Real, valid PDF bytes with one page per string in pages_text."""
    document = pymupdf.open()
    for text in pages_text:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


async def _make_user(db_session: AsyncSession, suffix: str):
    return await auth_service.create_user(
        db_session,
        email=f"doctextsvc_{suffix}@example.com",
        username=f"doctextsvc{suffix}",
        password="password123",
    )


async def _make_document_with_content(
    db_session: AsyncSession, user_id, *, filename: str, content: bytes, content_type: str = "application/pdf"
) -> Document:
    """
    Create a real Document via the real create_document() service —
    a real file lands on disk at exactly the path a real upload would
    produce, not a fabricated storage_path.
    """
    return await document_service.create_document(
        db_session,
        user_id=user_id,
        content=content,
        original_filename=filename,
        content_type=content_type,
    )


# --- 1. Successful single-page PDF parse + persistence ---


async def test_parse_and_store_single_page_pdf(db_session: AsyncSession):
    user = await _make_user(db_session, "single")
    document = await _make_document_with_content(
        db_session, user.id, filename="paper.pdf", content=_make_pdf_bytes(["Hello, ResearchPilot."])
    )

    document_text = await document_text_service.parse_and_store_document_text(
        db_session, document=document
    )

    assert document_text.document_id == document.id
    assert "Hello, ResearchPilot." in document_text.content

    # Confirm it's actually persisted, not just returned in-memory.
    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    fetched = result.scalar_one()
    assert fetched.content == document_text.content


# --- 2. Multi-page extraction/persistence ---


async def test_parse_and_store_multi_page_pdf_preserves_order(db_session: AsyncSession):
    user = await _make_user(db_session, "multipage")
    document = await _make_document_with_content(
        db_session,
        user.id,
        filename="multi.pdf",
        content=_make_pdf_bytes(["Page one content", "Page two content"]),
    )

    document_text = await document_text_service.parse_and_store_document_text(
        db_session, document=document
    )

    assert document_text.content.index("Page one content") < document_text.content.index(
        "Page two content"
    )


# --- 3. Valid PDF with no extractable text ---


async def test_parse_and_store_blank_pdf_persists_empty_string(db_session: AsyncSession):
    user = await _make_user(db_session, "blank")
    document = await _make_document_with_content(
        db_session, user.id, filename="blank.pdf", content=_make_pdf_bytes([""])
    )

    document_text = await document_text_service.parse_and_store_document_text(
        db_session, document=document
    )

    assert document_text.content == ""

    # Confirm the row genuinely exists (not skipped/absent) — an empty
    # string is a successful, persisted result, not a no-op.
    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    assert result.scalar_one() is not None


# --- 4. Corrupted PDF ---


async def test_parse_and_store_corrupted_pdf_raises_and_persists_nothing(db_session: AsyncSession):
    user = await _make_user(db_session, "corrupted")
    document = await _make_document_with_content(
        db_session,
        user.id,
        filename="corrupted.pdf",
        content=b"this is not a real pdf, just garbage bytes",
    )

    with pytest.raises(parse_service.ParseError):
        await document_text_service.parse_and_store_document_text(db_session, document=document)

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    assert result.scalar_one_or_none() is None


# --- 5. Missing file ---


async def test_parse_and_store_missing_file_raises_and_persists_nothing(db_session: AsyncSession):
    user = await _make_user(db_session, "missing")
    document = await _make_document_with_content(
        db_session, user.id, filename="willvanish.pdf", content=_make_pdf_bytes(["temporary"])
    )
    # Simulate the file having disappeared after the Document row was created.
    Path(document.storage_path).unlink()

    with pytest.raises(storage_service.StoredFileNotFoundError):
        await document_text_service.parse_and_store_document_text(db_session, document=document)

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    assert result.scalar_one_or_none() is None


# --- 6. Unsupported format ---


async def test_parse_and_store_docx_raises_unsupported_format_and_persists_nothing(
    db_session: AsyncSession,
):
    user = await _make_user(db_session, "docx")
    document = await _make_document_with_content(
        db_session,
        user.id,
        filename="notes.docx",
        content=b"pretend docx content",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    with pytest.raises(parse_service.UnsupportedFormatError):
        await document_text_service.parse_and_store_document_text(db_session, document=document)

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    assert result.scalar_one_or_none() is None


# --- 7. Reprocessing: update existing row, no duplicate ---


async def test_reparsing_updates_existing_row_without_creating_a_duplicate(
    db_session: AsyncSession,
):
    user = await _make_user(db_session, "reparse")
    document = await _make_document_with_content(
        db_session, user.id, filename="versioned.pdf", content=_make_pdf_bytes(["First version"])
    )

    first = await document_text_service.parse_and_store_document_text(db_session, document=document)
    first_id = first.id
    first_created_at = first.created_at

    # Simulate the underlying file being reprocessed with different content
    # (e.g. the document was re-uploaded/replaced at the same storage_path
    # in a real reprocessing scenario) by overwriting the file directly.
    Path(document.storage_path).write_bytes(_make_pdf_bytes(["Second version"]))

    second = await document_text_service.parse_and_store_document_text(db_session, document=document)

    assert second.id == first_id  # same row, not a new one
    assert second.content == "Second version"
    assert second.created_at == first_created_at  # first-parsed time preserved
    assert second.updated_at >= first.updated_at  # reparse time advanced

    # Confirm there is still exactly one DocumentText for this document.
    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == document.id)
    )
    assert len(result.scalars().all()) == 1


# --- 8. Persistence failure — real FK violation, not fabricated infrastructure ---


async def test_persistence_fails_cleanly_if_parent_document_no_longer_exists(
    db_session: AsyncSession,
):
    """
    A real (not simulated) database failure: the Document row is
    deleted out from under an already-fetched reference before the
    write is attempted, so the DocumentText insert legitimately
    violates the document_id -> documents.id FK constraint. This
    reuses the existing real-Postgres FK enforcement already relied
    on in test_document_text_model.py, rather than introducing any
    new fragile test infrastructure.
    """
    user = await _make_user(db_session, "fkfail")
    document = await _make_document_with_content(
        db_session, user.id, filename="doomed.pdf", content=_make_pdf_bytes(["about to be orphaned"])
    )

    # Resolve the file path and parse successfully first (parsing
    # itself doesn't touch the DB), then delete the parent Document
    # before the DocumentText write is attempted.
    await db_session.delete(document)
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await document_text_service.parse_and_store_document_text(db_session, document=document)
    await db_session.rollback()
