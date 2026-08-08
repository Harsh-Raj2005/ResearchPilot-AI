"""
Tests for POST /api/v1/documents/upload.

Task 3B Checkpoint 3's first HTTP-level tests for documents —
get_current_user's first real route consumer, exercised end-to-end
via the httpx client fixture. Redirects settings.upload_dir to a
tmp_path per test, same pattern as test_storage_service.py, so
nothing touches the real dev storage/uploads/ folder.
"""
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
