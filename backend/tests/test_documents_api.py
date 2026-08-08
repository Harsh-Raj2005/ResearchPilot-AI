"""
Tests for POST /api/v1/documents/upload.

Task 3B Checkpoint 3's first HTTP-level tests for documents —
get_current_user's first real route consumer, exercised end-to-end
via the httpx client fixture. Redirects settings.upload_dir to a
tmp_path per test, same pattern as test_storage_service.py, so
nothing touches the real dev storage/uploads/ folder.
"""
import asyncio
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    yield tmp_path


async def _signup_and_get_token(client: AsyncClient, *, email: str, username: str) -> str:
    await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "username": username, "password": "password123"},
    )
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "password123"}
    )
    return response.json()["access_token"]


async def _auth_headers(client: AsyncClient, *, email: str = "uploader@example.com",
                         username: str = "uploader") -> dict:
    token = await _signup_and_get_token(client, email=email, username=username)
    return {"Authorization": f"Bearer {token}"}


# --- authentication ---


async def test_upload_requires_authentication(client: AsyncClient):
    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 401


async def test_upload_rejects_invalid_token(client: AsyncClient):
    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


# --- success cases ---


async def test_upload_pdf_success(client: AsyncClient):
    headers = await _auth_headers(client)
    content = b"%PDF-1.4 fake pdf content"

    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("thesis.pdf", content, "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == "thesis.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["file_size_bytes"] == len(content)
    assert "id" in body
    assert "created_at" in body


async def test_upload_docx_success(client: AsyncClient):
    headers = await _auth_headers(client, email="docx@example.com", username="docxuser")
    response = await client.post(
        "/api/v1/documents/upload",
        files={
            "file": (
                "notes.docx",
                b"fake docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["original_filename"] == "notes.docx"


async def test_upload_txt_success(client: AsyncClient):
    headers = await _auth_headers(client, email="txt@example.com", username="txtuser")
    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("readme.txt", b"plain text content", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["original_filename"] == "readme.txt"


async def test_upload_response_does_not_leak_internal_storage_fields(client: AsyncClient):
    headers = await _auth_headers(client, email="leak@example.com", username="leakuser")
    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    body = response.json()
    assert "stored_filename" not in body
    assert "storage_path" not in body
    assert "user_id" not in body


async def test_upload_actually_writes_file_to_disk(client: AsyncClient, tmp_path):
    headers = await _auth_headers(client, email="disk@example.com", username="diskuser")
    content = b"content that must actually exist on disk afterward"

    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", content, "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 201

    upload_dir = Path(settings.upload_dir)
    saved_files = list(upload_dir.glob("*.pdf"))
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == content


# --- validation failures ---


async def test_upload_rejects_disallowed_extension(client: AsyncClient):
    headers = await _auth_headers(client, email="exe@example.com", username="exeuser")
    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("virus.exe", b"malicious", "application/x-msdownload")},
        headers=headers,
    )
    assert response.status_code == 422


async def test_upload_rejects_oversized_file(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)  # 0MB -> anything nonempty is "too large"
    headers = await _auth_headers(client, email="big@example.com", username="biguser")

    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"just a few bytes over a 0MB cap", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 413
    upload_dir = Path(settings.upload_dir)
    # Nothing should have been written — the size check happens before storage_service is called.
    assert not upload_dir.exists() or not list(upload_dir.glob("*"))


# --- multi-user isolation ---


async def test_two_users_can_upload_independently(client: AsyncClient):
    headers_a = await _auth_headers(client, email="usera@example.com", username="usera")
    headers_b = await _auth_headers(client, email="userb@example.com", username="userb")

    response_a = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("a.pdf", b"user a content", "application/pdf")},
        headers=headers_a,
    )
    response_b = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("b.pdf", b"user b content", "application/pdf")},
        headers=headers_b,
    )

    assert response_a.status_code == 201
    assert response_b.status_code == 201
    assert response_a.json()["id"] != response_b.json()["id"]


# --- GET /documents (Document Management CRUD, Checkpoint 1: listing) ---


async def test_list_documents_requires_authentication(client: AsyncClient):
    response = await client.get("/api/v1/documents")
    assert response.status_code == 401


async def test_list_documents_empty_for_user_with_no_documents(client: AsyncClient):
    headers = await _auth_headers(client, email="empty@example.com", username="emptyuser")
    response = await client.get("/api/v1/documents", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


async def test_list_documents_returns_own_documents(client: AsyncClient):
    headers = await _auth_headers(client, email="lister@example.com", username="lister")
    await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"content", "application/pdf")},
        headers=headers,
    )

    response = await client.get("/api/v1/documents", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["original_filename"] == "paper.pdf"
    assert body[0]["content_type"] == "application/pdf"
    assert "id" in body[0]
    assert "created_at" in body[0]


async def test_list_documents_does_not_leak_internal_fields(client: AsyncClient):
    headers = await _auth_headers(client, email="listleak@example.com", username="listleak")
    await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"content", "application/pdf")},
        headers=headers,
    )

    response = await client.get("/api/v1/documents", headers=headers)
    body = response.json()[0]
    assert "stored_filename" not in body
    assert "storage_path" not in body
    assert "user_id" not in body


async def test_list_documents_isolates_users(client: AsyncClient):
    headers_a = await _auth_headers(client, email="isoa@example.com", username="isoa")
    headers_b = await _auth_headers(client, email="isob@example.com", username="isob")

    await client.post(
        "/api/v1/documents/upload",
        files={"file": ("a_only.pdf", b"a content", "application/pdf")},
        headers=headers_a,
    )

    # User A sees their own document.
    response_a = await client.get("/api/v1/documents", headers=headers_a)
    assert response_a.status_code == 200
    assert len(response_a.json()) == 1
    assert response_a.json()[0]["original_filename"] == "a_only.pdf"

    # User B, who uploaded nothing, must not see user A's document.
    response_b = await client.get("/api/v1/documents", headers=headers_b)
    assert response_b.status_code == 200
    assert response_b.json() == []


async def test_list_documents_ordering_newest_first(client: AsyncClient):
    headers = await _auth_headers(client, email="order@example.com", username="orderuser")

    await client.post(
        "/api/v1/documents/upload",
        files={"file": ("first.pdf", b"first", "application/pdf")},
        headers=headers,
    )
    # A real gap between inserts, so created_at is guaranteed to differ —
    # same reasoning as the updated_at-ordering tests from Task 2.1.
    await asyncio.sleep(1.1)
    await client.post(
        "/api/v1/documents/upload",
        files={"file": ("second.pdf", b"second", "application/pdf")},
        headers=headers,
    )

    response = await client.get("/api/v1/documents", headers=headers)
    body = response.json()

    assert len(body) == 2
    assert body[0]["original_filename"] == "second.pdf"  # newest first
    assert body[1]["original_filename"] == "first.pdf"


async def test_list_documents_pagination_limit(client: AsyncClient):
    headers = await _auth_headers(client, email="paglimit@example.com", username="paglimit")
    for i in range(3):
        await client.post(
            "/api/v1/documents/upload",
            files={"file": (f"doc{i}.pdf", f"content{i}".encode(), "application/pdf")},
            headers=headers,
        )

    response = await client.get("/api/v1/documents?limit=2", headers=headers)

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_list_documents_pagination_skip(client: AsyncClient):
    headers = await _auth_headers(client, email="pagskip@example.com", username="pagskip")
    for i in range(3):
        await client.post(
            "/api/v1/documents/upload",
            files={"file": (f"doc{i}.pdf", f"content{i}".encode(), "application/pdf")},
            headers=headers,
        )

    all_docs = await client.get("/api/v1/documents?limit=10", headers=headers)
    skipped = await client.get("/api/v1/documents?skip=1&limit=10", headers=headers)

    assert len(all_docs.json()) == 3
    assert len(skipped.json()) == 2
    # skip=1 must skip exactly the newest (first) item from the unskipped list.
    assert skipped.json()[0]["id"] == all_docs.json()[1]["id"]


async def test_list_documents_rejects_limit_too_high(client: AsyncClient):
    headers = await _auth_headers(client, email="pagbound1@example.com", username="pagbound1")
    response = await client.get("/api/v1/documents?limit=101", headers=headers)
    assert response.status_code == 422


async def test_list_documents_rejects_limit_too_low(client: AsyncClient):
    headers = await _auth_headers(client, email="pagbound2@example.com", username="pagbound2")
    response = await client.get("/api/v1/documents?limit=0", headers=headers)
    assert response.status_code == 422


async def test_list_documents_rejects_negative_skip(client: AsyncClient):
    headers = await _auth_headers(client, email="pagbound3@example.com", username="pagbound3")
    response = await client.get("/api/v1/documents?skip=-1", headers=headers)
    assert response.status_code == 422


async def test_list_documents_default_pagination(client: AsyncClient):
    headers = await _auth_headers(client, email="pagdefault@example.com", username="pagdefault")
    await client.post(
        "/api/v1/documents/upload",
        files={"file": ("only.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    response = await client.get("/api/v1/documents", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1


# --- GET /documents/{document_id} (Document Management CRUD, Checkpoint 2: detail) ---


async def test_get_document_returns_own_document(client: AsyncClient):
    headers = await _auth_headers(client, email="detailowner@example.com", username="detailowner")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("thesis.pdf", b"thesis content", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    response = await client.get(f"/api/v1/documents/{document_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document_id
    assert body["original_filename"] == "thesis.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["file_size_bytes"] == len(b"thesis content")
    assert "created_at" in body


async def test_get_document_requires_authentication(client: AsyncClient):
    response = await client.get(f"/api/v1/documents/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_get_document_nonexistent_returns_404(client: AsyncClient):
    headers = await _auth_headers(client, email="detailmissing@example.com", username="detailmissing")
    response = await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_get_document_cannot_retrieve_another_users_document(client: AsyncClient):
    headers_a = await _auth_headers(client, email="detaila@example.com", username="detaila")
    headers_b = await _auth_headers(client, email="detailb@example.com", username="detailb")

    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("a_private.pdf", b"private", "application/pdf")},
        headers=headers_a,
    )
    document_id = upload.json()["id"]

    response = await client.get(f"/api/v1/documents/{document_id}", headers=headers_b)
    assert response.status_code == 404


async def test_get_document_wrong_owner_indistinguishable_from_nonexistent(client: AsyncClient):
    headers_a = await _auth_headers(client, email="indista@example.com", username="indista")
    headers_b = await _auth_headers(client, email="indistb@example.com", username="indistb")

    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("owned.pdf", b"owned", "application/pdf")},
        headers=headers_a,
    )
    owned_id = upload.json()["id"]

    wrong_owner_response = await client.get(f"/api/v1/documents/{owned_id}", headers=headers_b)
    nonexistent_response = await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=headers_b)

    assert wrong_owner_response.status_code == nonexistent_response.status_code == 404
    assert wrong_owner_response.json() == nonexistent_response.json()


async def test_get_document_does_not_leak_internal_fields(client: AsyncClient):
    headers = await _auth_headers(client, email="detailleak@example.com", username="detailleak")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    response = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
    body = response.json()
    assert "stored_filename" not in body
    assert "storage_path" not in body
    assert "user_id" not in body


async def test_get_document_invalid_id_format_rejected(client: AsyncClient):
    headers = await _auth_headers(client, email="detailbadid@example.com", username="detailbadid")
    response = await client.get("/api/v1/documents/not-a-uuid", headers=headers)
    assert response.status_code == 422


async def test_upload_and_list_still_work_after_detail_endpoint_added(client: AsyncClient):
    """Regression check: adding GET /{document_id} must not change /upload or list behavior."""
    headers = await _auth_headers(client, email="regression@example.com", username="regression")

    upload_response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("regress.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    assert upload_response.status_code == 201

    list_response = await client.get("/api/v1/documents", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
