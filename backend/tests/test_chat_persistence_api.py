"""
Tests for the four Chat Persistence HTTP endpoints:
- POST   /documents/{document_id}/chat/sessions
- GET    /documents/{document_id}/chat/sessions
- GET    /documents/{document_id}/chat/sessions/{session_id}/messages
- POST   /documents/{document_id}/chat/sessions/{session_id}/messages

Mirrors test_document_chat_api.py's conventions exactly (isolated
upload dir, signup+login for a bearer token, real PDF upload via the
actual upload endpoint). The mocking boundary for the send-message
tests is rag_service.answer_question_with_history() itself — this
file's job is to test the routers' orchestration (auth -> ownership
-> nested session ownership -> persistence -> RAG call -> response),
not RAG's own internal correctness (already covered by
test_rag_service.py) or chat_session_service's own internal
correctness (already covered by test_chat_session_service.py).

No real OpenAI API call is made anywhere in this file.
"""
import uuid

import pymupdf
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_message import ChatMessage
from app.services import rag_service


def _make_pdf_bytes(text: str = "placeholder") -> bytes:
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
    client: AsyncClient, *, email: str = "persistuser@example.com", username: str = "persistuser"
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


async def _create_session(client: AsyncClient, headers: dict, document_id: str) -> str:
    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions", headers=headers
    )
    assert response.status_code == 201
    return response.json()["id"]


def _mock_answer_question_with_history(monkeypatch, *, answer: str = "mocked answer", error=None):
    """
    Mocks rag_service.answer_question_with_history() itself — the
    only network-adjacent boundary this file needs to control.
    """
    calls = []

    async def _fake(db, *, document, question, history, top_k=None):
        calls.append(
            {"db": db, "document": document, "question": question, "history": history, "top_k": top_k}
        )
        if error is not None:
            raise error
        return answer

    monkeypatch.setattr(rag_service, "answer_question_with_history", _fake)
    return calls


# =====================================================================
# POST /documents/{document_id}/chat/sessions — create session
# =====================================================================


async def test_create_session_success_returns_expected_shape(client: AsyncClient):
    headers = await _auth_headers(client, email="createok@example.com", username="createok")
    document_id = await _upload_pdf(client, headers)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions", headers=headers
    )

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert "created_at" in body
    uuid.UUID(body["id"])  # must be a real UUID


async def test_create_session_requires_authentication(client: AsyncClient):
    response = await client.post(f"/api/v1/documents/{uuid.uuid4()}/chat/sessions")
    assert response.status_code == 401


async def test_create_session_rejects_invalid_token(client: AsyncClient):
    response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat/sessions",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


async def test_create_session_nonexistent_document_returns_404(client: AsyncClient):
    headers = await _auth_headers(client, email="createmissing@example.com", username="createmissing")

    response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat/sessions", headers=headers
    )

    assert response.status_code == 404


async def test_create_session_another_users_document_returns_404(client: AsyncClient):
    headers_a = await _auth_headers(client, email="createa@example.com", username="createa")
    headers_b = await _auth_headers(client, email="createb@example.com", username="createb")
    document_id = await _upload_pdf(client, headers_a)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions", headers=headers_b
    )
    nonexistent = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat/sessions", headers=headers_b
    )

    assert response.status_code == nonexistent.status_code == 404
    assert response.json() == nonexistent.json()


async def test_create_session_invalid_document_uuid_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="createuuid@example.com", username="createuuid")

    response = await client.post(
        "/api/v1/documents/not-a-uuid/chat/sessions", headers=headers
    )

    assert response.status_code == 422


# =====================================================================
# GET /documents/{document_id}/chat/sessions — list sessions
# =====================================================================


async def test_list_sessions_returns_created_sessions(client: AsyncClient):
    headers = await _auth_headers(client, email="listok@example.com", username="listok")
    document_id = await _upload_pdf(client, headers)
    session_a = await _create_session(client, headers, document_id)
    session_b = await _create_session(client, headers, document_id)

    response = await client.get(
        f"/api/v1/documents/{document_id}/chat/sessions", headers=headers
    )

    assert response.status_code == 200
    ids = {s["id"] for s in response.json()}
    assert ids == {session_a, session_b}


async def test_list_sessions_requires_authentication(client: AsyncClient):
    response = await client.get(f"/api/v1/documents/{uuid.uuid4()}/chat/sessions")
    assert response.status_code == 401


async def test_list_sessions_nonexistent_document_returns_404(client: AsyncClient):
    headers = await _auth_headers(client, email="listmissing@example.com", username="listmissing")

    response = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/chat/sessions", headers=headers
    )

    assert response.status_code == 404


async def test_list_sessions_another_users_document_returns_404(client: AsyncClient):
    headers_a = await _auth_headers(client, email="lista@example.com", username="lista")
    headers_b = await _auth_headers(client, email="listb@example.com", username="listb")
    document_id = await _upload_pdf(client, headers_a)
    await _create_session(client, headers_a, document_id)

    response = await client.get(
        f"/api/v1/documents/{document_id}/chat/sessions", headers=headers_b
    )

    assert response.status_code == 404


async def test_list_sessions_excludes_another_documents_sessions(client: AsyncClient):
    headers = await _auth_headers(client, email="listiso@example.com", username="listiso")
    document_a = await _upload_pdf(client, headers, filename="a.pdf")
    document_b = await _upload_pdf(client, headers, filename="b.pdf")
    session_a = await _create_session(client, headers, document_a)
    await _create_session(client, headers, document_b)

    response = await client.get(
        f"/api/v1/documents/{document_a}/chat/sessions", headers=headers
    )

    ids = {s["id"] for s in response.json()}
    assert ids == {session_a}


# =====================================================================
# GET /documents/{document_id}/chat/sessions/{session_id}/messages
# =====================================================================


async def test_list_messages_requires_authentication(client: AsyncClient):
    response = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/chat/sessions/{uuid.uuid4()}/messages"
    )
    assert response.status_code == 401


async def test_list_messages_returns_empty_list_for_new_session(client: AsyncClient):
    headers = await _auth_headers(client, email="listmsgempty@example.com", username="listmsgempty")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)

    response = await client.get(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages", headers=headers
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_messages_nonexistent_document_returns_404(client: AsyncClient):
    headers = await _auth_headers(client, email="listmsgnodoc@example.com", username="listmsgnodoc")

    response = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/chat/sessions/{uuid.uuid4()}/messages", headers=headers
    )

    assert response.status_code == 404


async def test_list_messages_nonexistent_session_returns_404(client: AsyncClient):
    headers = await _auth_headers(client, email="listmsgnosess@example.com", username="listmsgnosess")
    document_id = await _upload_pdf(client, headers)

    response = await client.get(
        f"/api/v1/documents/{document_id}/chat/sessions/{uuid.uuid4()}/messages", headers=headers
    )

    assert response.status_code == 404


async def test_list_messages_another_users_document_returns_404(client: AsyncClient):
    headers_a = await _auth_headers(client, email="listmsga@example.com", username="listmsga")
    headers_b = await _auth_headers(client, email="listmsgb@example.com", username="listmsgb")
    document_id = await _upload_pdf(client, headers_a)
    session_id = await _create_session(client, headers_a, document_id)

    response = await client.get(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages", headers=headers_b
    )

    assert response.status_code == 404


async def test_list_messages_session_id_from_different_document_returns_404_idor(
    client: AsyncClient,
):
    """
    IDOR protection: a session that genuinely exists and is owned by
    this same user, but under a DIFFERENT document, must not be
    reachable via another document's URL — even though both documents
    (and the session) all belong to the same authenticated user. This
    is the nested nested-ownership check's entire reason for existing.
    """
    headers = await _auth_headers(client, email="idor@example.com", username="idor")
    document_a = await _upload_pdf(client, headers, filename="a.pdf")
    document_b = await _upload_pdf(client, headers, filename="b.pdf")
    session_under_a = await _create_session(client, headers, document_a)

    response = await client.get(
        f"/api/v1/documents/{document_b}/chat/sessions/{session_under_a}/messages", headers=headers
    )

    assert response.status_code == 404


async def test_list_messages_returns_messages_in_order(client: AsyncClient, monkeypatch):
    headers = await _auth_headers(client, email="listmsgorder@example.com", username="listmsgorder")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)
    _mock_answer_question_with_history(monkeypatch, answer="first answer")

    await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "first question"},
        headers=headers,
    )

    response = await client.get(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages", headers=headers
    )

    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "first question"
    assert messages[0]["sequence_number"] == 0
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "first answer"
    assert messages[1]["sequence_number"] == 1


# =====================================================================
# POST /documents/{document_id}/chat/sessions/{session_id}/messages
# =====================================================================


async def test_send_message_success_returns_assistant_message(client: AsyncClient, monkeypatch):
    headers = await _auth_headers(client, email="sendok@example.com", username="sendok")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)
    _mock_answer_question_with_history(monkeypatch, answer="The answer is X.")

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "What is X?"},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "assistant"
    assert body["content"] == "The answer is X."
    assert body["sequence_number"] == 1  # user message is 0, this is 1
    assert "id" in body
    assert "created_at" in body


async def test_send_message_requires_authentication(client: AsyncClient):
    response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat/sessions/{uuid.uuid4()}/messages",
        json={"question": "question"},
    )
    assert response.status_code == 401


async def test_send_message_nonexistent_document_returns_404(client: AsyncClient, monkeypatch):
    headers = await _auth_headers(client, email="sendnodoc@example.com", username="sendnodoc")
    calls = _mock_answer_question_with_history(monkeypatch)

    response = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/chat/sessions/{uuid.uuid4()}/messages",
        json={"question": "question"},
        headers=headers,
    )

    assert response.status_code == 404
    assert calls == []


async def test_send_message_nonexistent_session_returns_404(client: AsyncClient, monkeypatch):
    headers = await _auth_headers(client, email="sendnosess@example.com", username="sendnosess")
    document_id = await _upload_pdf(client, headers)
    calls = _mock_answer_question_with_history(monkeypatch)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{uuid.uuid4()}/messages",
        json={"question": "question"},
        headers=headers,
    )

    assert response.status_code == 404
    assert calls == []


async def test_send_message_another_users_document_returns_404(client: AsyncClient, monkeypatch):
    headers_a = await _auth_headers(client, email="senda@example.com", username="senda")
    headers_b = await _auth_headers(client, email="sendb@example.com", username="sendb")
    document_id = await _upload_pdf(client, headers_a)
    session_id = await _create_session(client, headers_a, document_id)
    calls = _mock_answer_question_with_history(monkeypatch)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "question"},
        headers=headers_b,
    )

    assert response.status_code == 404
    assert calls == []


async def test_send_message_session_id_from_different_document_returns_404_idor(
    client: AsyncClient, monkeypatch
):
    """Same IDOR protection as the GET messages test, applied to the
    write path — arguably more important here, since it would
    otherwise allow writing messages into a session under a document
    the caller didn't reference in the URL."""
    headers = await _auth_headers(client, email="sendidor@example.com", username="sendidor")
    document_a = await _upload_pdf(client, headers, filename="a.pdf")
    document_b = await _upload_pdf(client, headers, filename="b.pdf")
    session_under_a = await _create_session(client, headers, document_a)
    calls = _mock_answer_question_with_history(monkeypatch)

    response = await client.post(
        f"/api/v1/documents/{document_b}/chat/sessions/{session_under_a}/messages",
        json={"question": "question"},
        headers=headers,
    )

    assert response.status_code == 404
    assert calls == []


async def test_send_message_empty_question_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="sendempty@example.com", username="sendempty")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": ""},
        headers=headers,
    )

    assert response.status_code == 422


async def test_send_message_whitespace_only_question_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="sendws@example.com", username="sendws")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "   "},
        headers=headers,
    )

    assert response.status_code == 422


async def test_send_message_missing_question_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="sendmissing@example.com", username="sendmissing")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={},
        headers=headers,
    )

    assert response.status_code == 422


async def test_send_message_persists_both_user_and_assistant_messages(
    client: AsyncClient, monkeypatch
):
    headers = await _auth_headers(client, email="sendpersist@example.com", username="sendpersist")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)
    _mock_answer_question_with_history(monkeypatch, answer="persisted answer")

    await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "persisted question"},
        headers=headers,
    )

    response = await client.get(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages", headers=headers
    )
    messages = response.json()
    assert len(messages) == 2
    assert messages[0]["content"] == "persisted question"
    assert messages[1]["content"] == "persisted answer"


async def test_send_message_second_call_receives_prior_history(client: AsyncClient, monkeypatch):
    headers = await _auth_headers(client, email="sendhistory@example.com", username="sendhistory")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)
    calls = _mock_answer_question_with_history(monkeypatch, answer="second answer")

    # First message — establish some history via a direct API call
    # that we don't need to inspect closely.
    first_calls = _mock_answer_question_with_history(monkeypatch, answer="first answer")
    await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "first question"},
        headers=headers,
    )
    assert first_calls[0]["history"] == []  # no prior history on the very first message

    # Second message — the RAG service must now receive the first
    # question and its answer as history, in order.
    calls = _mock_answer_question_with_history(monkeypatch, answer="second answer")
    await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "second question"},
        headers=headers,
    )

    assert calls[0]["history"] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]
    assert calls[0]["question"] == "second question"


async def test_send_message_llm_provider_error_returns_502(client: AsyncClient, monkeypatch):
    from app.services import llm_service

    headers = await _auth_headers(client, email="sendllmfail@example.com", username="sendllmfail")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)
    error = llm_service.LLMProviderError(
        "raw provider stack trace / api key / internal detail that must never leak"
    )
    _mock_answer_question_with_history(monkeypatch, error=error)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "question"},
        headers=headers,
    )

    assert response.status_code == 502
    body = response.json()
    assert "raw provider stack trace" not in body["detail"]
    assert "api key" not in body["detail"].lower()


async def test_send_message_llm_failure_persists_no_messages_atomicity(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """
    The critical atomicity test: if the RAG/LLM call fails, NEITHER
    the user's message NOR any assistant message may be durably
    persisted — not even the user's own question. This proves the
    _stage_message()-then-single-commit fix actually works over real
    HTTP, not just at the service-test level already covered by
    test_chat_session_service.py's own atomicity tests.

    The test client's get_db override shares one session across the
    whole test (unlike production's get_db, which wraps each request
    in `async with AsyncSessionLocal()` and rolls back automatically
    on any propagating exception via AsyncSession.close()). Rolling
    back explicitly here reproduces that same production guarantee
    for this same-session query, exactly like
    test_document_process_api.py's own embedding-failure test already
    does for the Document Chunks -> Embeddings milestone.
    """
    from app.services import llm_service

    headers = await _auth_headers(client, email="sendatomicfail@example.com", username="sendatomicfail")
    document_id = await _upload_pdf(client, headers)
    session_id = await _create_session(client, headers, document_id)
    error = llm_service.LLMProviderError("simulated provider failure")
    _mock_answer_question_with_history(monkeypatch, error=error)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages",
        json={"question": "this question must not survive"},
        headers=headers,
    )

    assert response.status_code == 502

    # Reproduce production's implicit rollback-on-exception, since the
    # test client's get_db override doesn't do it automatically.
    await db_session.rollback()

    result = await db_session.execute(
        select(ChatMessage).where(ChatMessage.chat_session_id == uuid.UUID(session_id))
    )
    assert result.scalars().all() == []

    # Also confirmed via the HTTP-level GET, using a fresh request:
    response = await client.get(
        f"/api/v1/documents/{document_id}/chat/sessions/{session_id}/messages", headers=headers
    )
    assert response.json() == []


async def test_send_message_invalid_session_uuid_returns_422(client: AsyncClient):
    headers = await _auth_headers(client, email="sendbaduuid@example.com", username="sendbaduuid")
    document_id = await _upload_pdf(client, headers)

    response = await client.post(
        f"/api/v1/documents/{document_id}/chat/sessions/not-a-uuid/messages",
        json={"question": "question"},
        headers=headers,
    )

    assert response.status_code == 422


# --- Mandatory cross-user IDOR: User B's OWN document + User A's session id ---


async def test_idor_user_b_own_document_plus_user_a_session_id_returns_404(
    client: AsyncClient, monkeypatch
):
    """
    The strictest IDOR combination: User A owns Document A and
    Session A. User B owns a completely separate Document B. User B
    must not be able to reach Session A's messages by splicing its
    UUID into User B's own, legitimately-owned document path. Neither
    document ownership alone (User B does own Document B) nor
    knowledge of session_id alone is sufficient — both conditions
    (document ownership AND session-belongs-to-that-document) must
    hold together.
    """
    headers_a = await _auth_headers(client, email="idorstricta@example.com", username="idorstricta")
    headers_b = await _auth_headers(client, email="idorstrictb@example.com", username="idorstrictb")

    document_a = await _upload_pdf(client, headers_a, filename="a.pdf")
    document_b = await _upload_pdf(client, headers_b, filename="b.pdf")
    session_a = await _create_session(client, headers_a, document_a)

    # GET history via User B's own document + User A's session id.
    get_response = await client.get(
        f"/api/v1/documents/{document_b}/chat/sessions/{session_a}/messages", headers=headers_b
    )
    assert get_response.status_code == 404

    # POST a message via the same cross-wired path — RAG must never
    # be invoked.
    calls = _mock_answer_question_with_history(monkeypatch)
    post_response = await client.post(
        f"/api/v1/documents/{document_b}/chat/sessions/{session_a}/messages",
        json={"question": "attempted cross-user access"},
        headers=headers_b,
    )
    assert post_response.status_code == 404
    assert calls == []

    # For completeness, the already-covered direct path (User A's own
    # document + User A's session, accessed by User B) must also fail.
    direct_response = await client.get(
        f"/api/v1/documents/{document_a}/chat/sessions/{session_a}/messages", headers=headers_b
    )
    assert direct_response.status_code == 404


async def test_idor_404_responses_are_indistinguishable(client: AsyncClient):
    """
    A 404 for a genuinely nonexistent session and a 404 for a
    real-but-unauthorized session must be indistinguishable — no
    detail difference that would let an attacker infer a session
    exists but belongs to someone else.
    """
    headers_a = await _auth_headers(client, email="idorindista@example.com", username="idorindista")
    headers_b = await _auth_headers(client, email="idorindistb@example.com", username="idorindistb")
    document_a = await _upload_pdf(client, headers_a)
    session_a = await _create_session(client, headers_a, document_a)

    real_session_response = await client.get(
        f"/api/v1/documents/{document_a}/chat/sessions/{session_a}/messages", headers=headers_b
    )
    fake_session_response = await client.get(
        f"/api/v1/documents/{document_a}/chat/sessions/{uuid.uuid4()}/messages", headers=headers_b
    )

    assert real_session_response.status_code == fake_session_response.status_code == 404
    assert real_session_response.json() == fake_session_response.json()

