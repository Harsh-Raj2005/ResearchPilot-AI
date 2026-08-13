"""
Tests for POST /api/v1/documents/{document_id}/process.

Document Text Extraction Checkpoint 5's first HTTP-level tests — the
explicit processing endpoint that wires document_text_service's
already-tested parse_and_store_document_text() into a real route for
the first time. A dedicated file rather than folding these into
test_documents_api.py: mirrors the same service-level split already
made between document_service and document_text_service (see that
file's own docstring) — this is a new feature domain (text
extraction), not another CRUD operation on Document metadata.

Same isolation and authentication-helper conventions as
test_documents_api.py (isolated upload_dir per test, signup+login for
a bearer token) and the same real-PDF-generation style as
test_parse_service.py / test_document_text_service.py — no mocking of
parse_service, storage_service, or document_text_service anywhere.
"""
import uuid
from pathlib import Path

import pymupdf
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document_chunk import DocumentChunk
from app.models.document_text import DocumentText
from app.services import embedding_service


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    yield tmp_path


@pytest.fixture(autouse=True)
def _mock_embed_texts(monkeypatch):
    """No test in this file ever calls the real OpenAI API."""

    async def _fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [[0.5] * 1536 for _ in texts]

    monkeypatch.setattr(embedding_service, "embed_texts", _fake_embed_texts)
    yield _fake_embed_texts


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


async def _signup_and_get_token(client: AsyncClient, *, email: str, username: str) -> str:
    await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "username": username, "password": "password123"},
    )
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "password123"}
    )
    return response.json()["access_token"]


async def _auth_headers(
    client: AsyncClient, *, email: str = "processor@example.com", username: str = "processor"
) -> dict:
    token = await _signup_and_get_token(client, email=email, username=username)
    return {"Authorization": f"Bearer {token}"}


async def _upload_pdf(client: AsyncClient, headers: dict, *, filename: str, content: bytes) -> str:
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, content, "application/pdf")},
        headers=headers,
    )
    assert upload.status_code == 201
    return upload.json()["id"]


# --- 1. Successful processing of an owned PDF ---


async def test_process_document_success(client: AsyncClient, db_session: AsyncSession):
    headers = await _auth_headers(client, email="processok@example.com", username="processok")
    document_id = await _upload_pdf(
        client, headers, filename="paper.pdf", content=_make_pdf_bytes(["Hello, ResearchPilot."])
    )

    response = await client.post(f"/api/v1/documents/{document_id}/process", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document_id
    assert body["original_filename"] == "paper.pdf"
    assert body["content_type"] == "application/pdf"
    assert "file_size_bytes" in body
    assert "created_at" in body

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == uuid.UUID(document_id))
    )
    document_text = result.scalar_one()
    assert document_text.content == "Hello, ResearchPilot."

    # Chunks are also persisted in the same request (Document Chunking
    # milestone) — never exposed in the response, but real rows exist.
    chunk_result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == document_text.id)
    )
    chunks = chunk_result.scalars().all()
    assert len(chunks) == 1
    assert chunks[0].content == "Hello, ResearchPilot."
    assert chunks[0].chunk_index == 0


# --- 2. Reprocessing an already-processed document ---


async def test_process_document_reprocessing_updates_in_place(
    client: AsyncClient, db_session: AsyncSession
):
    headers = await _auth_headers(client, email="reprocess@example.com", username="reprocess")
    document_id = await _upload_pdf(
        client, headers, filename="paper.pdf", content=_make_pdf_bytes(["First version"])
    )

    first = await client.post(f"/api/v1/documents/{document_id}/process", headers=headers)
    assert first.status_code == 200

    second = await client.post(f"/api/v1/documents/{document_id}/process", headers=headers)
    assert second.status_code == 200

    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == uuid.UUID(document_id))
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "First version"

    # Chunks are replaced (not duplicated) on reprocessing too.
    chunk_result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_text_id == rows[0].id)
    )
    chunks = chunk_result.scalars().all()
    assert len(chunks) == 1
    assert chunks[0].content == "First version"


# --- 3. Nonexistent document ---


async def test_process_document_nonexistent_returns_404(client: AsyncClient):
    headers = await _auth_headers(client, email="processmissing@example.com", username="processmissing")
    response = await client.post(f"/api/v1/documents/{uuid.uuid4()}/process", headers=headers)
    assert response.status_code == 404


# --- 4. Document belonging to another user ---


async def test_process_document_wrong_owner_returns_404_no_leak(
    client: AsyncClient, db_session: AsyncSession
):
    headers_a = await _auth_headers(client, email="processa@example.com", username="processa")
    headers_b = await _auth_headers(client, email="processb@example.com", username="processb")

    document_id = await _upload_pdf(
        client, headers_a, filename="a_owned.pdf", content=_make_pdf_bytes(["Owner A's content"])
    )

    wrong_owner_response = await client.post(
        f"/api/v1/documents/{document_id}/process", headers=headers_b
    )
    nonexistent_response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/process", headers=headers_b
    )

    assert wrong_owner_response.status_code == nonexistent_response.status_code == 404
    assert wrong_owner_response.json() == nonexistent_response.json()

    # Confirm nothing was actually processed for the other user's document.
    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == uuid.UUID(document_id))
    )
    assert result.scalar_one_or_none() is None
    # No chunks either, since no DocumentText was ever created.
    chunk_result = await db_session.execute(select(DocumentChunk))
    assert chunk_result.scalars().first() is None


# --- 5. Unsupported format ---


async def test_process_document_unsupported_format_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    headers = await _auth_headers(client, email="processdocx@example.com", username="processdocx")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={
            "file": (
                "notes.docx",
                b"fake docx content",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=headers,
    )
    assert upload.status_code == 201
    document_id = upload.json()["id"]

    response = await client.post(f"/api/v1/documents/{document_id}/process", headers=headers)

    assert response.status_code == 422
    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == uuid.UUID(document_id))
    )
    assert result.scalar_one_or_none() is None
    chunk_result = await db_session.execute(select(DocumentChunk))
    assert chunk_result.scalars().first() is None


# --- 6. Corrupted/invalid PDF ---


async def test_process_document_corrupted_pdf_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    headers = await _auth_headers(client, email="processcorrupt@example.com", username="processcorrupt")
    document_id = await _upload_pdf(
        client, headers, filename="corrupted.pdf", content=b"this is not a real pdf, just garbage bytes"
    )

    response = await client.post(f"/api/v1/documents/{document_id}/process", headers=headers)

    assert response.status_code == 422
    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == uuid.UUID(document_id))
    )
    assert result.scalar_one_or_none() is None
    chunk_result = await db_session.execute(select(DocumentChunk))
    assert chunk_result.scalars().first() is None


# --- 7. Missing stored file ---


async def test_process_document_missing_stored_file_returns_500(
    client: AsyncClient, db_session: AsyncSession, tmp_path
):
    headers = await _auth_headers(client, email="processnofile@example.com", username="processnofile")
    document_id = await _upload_pdf(
        client, headers, filename="willvanish.pdf", content=_make_pdf_bytes(["Soon gone"])
    )

    # Remove the file out from under the Document row — simulates a
    # real DB/filesystem drift (manual deletion, volume reset), same
    # scenario download's existing StoredFileNotFoundError handling
    # already covers.
    upload_dir = Path(settings.upload_dir)
    stored_files = list(upload_dir.glob("*.pdf"))
    assert len(stored_files) == 1
    stored_files[0].unlink()

    response = await client.post(f"/api/v1/documents/{document_id}/process", headers=headers)

    assert response.status_code == 500
    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == uuid.UUID(document_id))
    )
    assert result.scalar_one_or_none() is None
    chunk_result = await db_session.execute(select(DocumentChunk))
    assert chunk_result.scalars().first() is None


# --- 7b. Embedding provider failure (Document Chunks -> Embeddings milestone) ---


async def test_process_document_embedding_failure_returns_502(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    async def _failing_embed_texts(texts):
        raise embedding_service.EmbeddingProviderError(
            "raw provider stack trace / api key / internal detail that must never leak"
        )

    monkeypatch.setattr(embedding_service, "embed_texts", _failing_embed_texts)

    headers = await _auth_headers(client, email="processembedfail@example.com", username="processembedfail")
    document_id = await _upload_pdf(
        client, headers, filename="paper.pdf", content=_make_pdf_bytes(["Some real extractable text."])
    )

    response = await client.post(f"/api/v1/documents/{document_id}/process", headers=headers)

    assert response.status_code == 502
    body = response.json()
    # Generic, safe message only — no raw provider exception text leaks.
    assert "raw provider stack trace" not in body["detail"]
    assert "api key" not in body["detail"].lower()

    # The DocumentText upsert flushes (but never commits) before
    # embed_texts() runs — the test client's get_db override shares
    # one session across the whole test (unlike production's get_db,
    # which wraps each request in `async with AsyncSessionLocal()`
    # and rolls back automatically on any propagating exception via
    # AsyncSession.close()). Rolling back explicitly here reproduces
    # that same production guarantee for this same-session query,
    # exactly like test_document_processing_service.py's atomicity
    # tests already do.
    await db_session.rollback()

    # Nothing was persisted — same "no partial state" guarantee as
    # every other failure mode this endpoint already handles.
    result = await db_session.execute(
        select(DocumentText).where(DocumentText.document_id == uuid.UUID(document_id))
    )
    assert result.scalar_one_or_none() is None
    chunk_result = await db_session.execute(select(DocumentChunk))
    assert chunk_result.scalars().first() is None


# --- 8. Authentication ---


async def test_process_document_requires_authentication(client: AsyncClient):
    response = await client.post(f"/api/v1/documents/{uuid.uuid4()}/process")
    assert response.status_code == 401


async def test_process_document_rejects_invalid_token(client: AsyncClient):
    response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/process",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


# --- 9. Invalid UUID path parameter ---


async def test_process_document_invalid_id_format_rejected(client: AsyncClient):
    headers = await _auth_headers(client, email="processbadid@example.com", username="processbadid")
    response = await client.post("/api/v1/documents/not-a-uuid/process", headers=headers)
    assert response.status_code == 422


# --- 10. Response never exposes extracted text or internal storage details ---


async def test_process_document_response_excludes_internal_fields(client: AsyncClient):
    headers = await _auth_headers(client, email="processfields@example.com", username="processfields")
    document_id = await _upload_pdf(
        client, headers, filename="fields.pdf", content=_make_pdf_bytes(["Some extractable text"])
    )

    response = await client.post(f"/api/v1/documents/{document_id}/process", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert "content" not in body
    assert "extracted_text" not in body
    assert "storage_path" not in body
    assert "stored_filename" not in body
    assert "chunks" not in body
    assert "embedding" not in body
