"""
Tests for POST /api/v1/documents/{document_id}/chat.

Single-Document Chat API milestone's first HTTP-level tests — the
thin authenticated endpoint that exposes the existing internal
rag_service.answer_question() pipeline. Mirrors
test_document_process_api.py's conventions exactly (isolated upload
dir, signup+login for a bearer token, real PDF bytes, real document
creation via the actual upload endpoint) — the only new mocking
boundary is rag_service.answer_question() itself, since this file's
job is to test the router's orchestration (auth -> ownership ->
RAG call -> response), not the RAG pipeline's own internals (already
covered by test_rag_service.py).

No real OpenAI API call is made anywhere in this file — the RAG
service boundary is mocked at the point this router calls it.
"""
import uuid

import pymupdf
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import rag_service


def _make_pdf_bytes(text: str = "placeholder") -> bytes:
    """Real, valid PDF bytes — same style as
    test_document_process_api.py's own helper."""
    document = pymupdf.open()
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
    client: AsyncClient, *, email: str = "chatuser@example.com", username: str = "chatuser"
) -> dict:
    token = await _signup_and_get_token(client, email=email, username=username)
    return {"Authorization": f"Bearer {token}"}


async def _upload_pdf(client: AsyncClient, headers: dict, *, filename: str = "paper.pdf") -> str:
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, _make_pdf_bytes(), "application/pdf")},
        headers=headers,
    )
    assert upload.status_code == 201
    return upload.json()["id"]


def _mock_answer_question(monkeypatch, *, answer: str = "mocked answer", error=None):
    """
    Mocks rag_service.answer_question() itself — this file's job is
    to test the router's orchestration around it (auth, ownership,
    request/response shape, error mapping), not to re-verify RAG's
    own internal correctness (already covered by test_rag_service.py).
    """
    calls = []

    async def _fake_answer_question(db, *, document, question, top_k=None):
        calls.append({"db": db, "document": document, "question": question, "top_k": top_k})
        if error is not None:
            raise error
        return answer

    monkeypatch.setattr(rag_service, "answer_question", _fake_answer_question)
    return calls


# --- 1. Successful chat request ---


async def test_chat_success_returns_expected_shape(
    db_session: AsyncSession, client: AsyncClient, monkeypatch
):
    headers = await _auth_headers(client, email="chatok2@example.com", username="chatok2")
    document_id = await _upload_pdf(client, headers)
    _mock_answer_question(monkeypatch, answer="The main contribution is X.")

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat",
        json={"question": "What is the main contribution?"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "The main contribution is X."}


# --- 2. Authentication required ---


async def test_chat_requires_authentication(client: AsyncClient):
    response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat", json={"question": "question"}
    )
    assert response.status_code == 401


# --- 3. Invalid authentication ---


async def test_chat_rejects_invalid_token(client: AsyncClient):
    response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat",
        json={"question": "question"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


# --- 4. Non-existent document ---


async def test_chat_nonexistent_document_returns_404(client: AsyncClient, monkeypatch):
    headers = await _auth_headers(client, email="chatmissing@example.com", username="chatmissing")
    calls = _mock_answer_question(monkeypatch)

    response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat",
        json={"question": "question"},
        headers=headers,
    )

    assert response.status_code == 404
    assert calls == []  # RAG must never be called when ownership resolution fails


# --- 5. Cross-user ownership isolation ---


async def test_chat_another_users_document_returns_404_not_403(
    client: AsyncClient, monkeypatch
):
    headers_a = await _auth_headers(client, email="chata@example.com", username="chata")
    headers_b = await _auth_headers(client, email="chatb@example.com", username="chatb")
    document_id = await _upload_pdf(client, headers_a)

    calls = _mock_answer_question(monkeypatch)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat",
        json={"question": "question"},
        headers=headers_b,
    )

    # Deliberately 404, not 403 — get_document_for_user() makes
    # nonexistent and unauthorized documents indistinguishable.
    assert response.status_code == 404
    nonexistent_response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat",
        json={"question": "question"},
        headers=headers_b,
    )
    assert nonexistent_response.status_code == response.status_code
    assert nonexistent_response.json() == response.json()
    assert calls == []


# --- 6. Empty question ---


async def test_chat_empty_question_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="chatempty@example.com", username="chatempty")
    document_id = await _upload_pdf(client, headers)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat",
        json={"question": ""},
        headers=headers,
    )

    assert response.status_code == 422


# --- 7. Whitespace-only question ---


async def test_chat_whitespace_only_question_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="chatws@example.com", username="chatws")
    document_id = await _upload_pdf(client, headers)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat",
        json={"question": "   "},
        headers=headers,
    )

    assert response.status_code == 422


async def test_chat_missing_question_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="chatmissingq@example.com", username="chatmissingq")
    document_id = await _upload_pdf(client, headers)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat",
        json={},
        headers=headers,
    )

    assert response.status_code == 422


async def test_chat_invalid_uuid_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="chatuuid@example.com", username="chatuuid")

    response = await client.post(
        "/api/v1/documents/not-a-uuid/chat",
        json={"question": "question"},
        headers=headers,
    )

    assert response.status_code == 422


# --- 8. RAG service invocation — correct document, question, db session ---


async def test_chat_calls_rag_service_with_correct_arguments(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    headers = await _auth_headers(client, email="chatargs@example.com", username="chatargs")
    document_id = await _upload_pdf(client, headers)
    calls = _mock_answer_question(monkeypatch, answer="answer")

    await client.post(
        f"/api/v1/documents/{document_id}/chat",
        json={"question": "What is the main contribution?"},
        headers=headers,
    )

    assert len(calls) == 1
    assert str(calls[0]["document"].id) == document_id
    assert calls[0]["question"] == "What is the main contribution?"
    assert calls[0]["db"] is not None


# --- 9. LLMProviderError -> 502 ---


async def test_chat_llm_provider_error_returns_502(client: AsyncClient, monkeypatch):
    from app.services import llm_service

    headers = await _auth_headers(client, email="chatllmfail@example.com", username="chatllmfail")
    document_id = await _upload_pdf(client, headers)
    error = llm_service.LLMProviderError(
        "raw provider stack trace / api key / internal detail that must never leak"
    )
    _mock_answer_question(monkeypatch, error=error)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat",
        json={"question": "question"},
        headers=headers,
    )

    assert response.status_code == 502
    body = response.json()
    assert "raw provider stack trace" not in body["detail"]
    assert "api key" not in body["detail"].lower()
