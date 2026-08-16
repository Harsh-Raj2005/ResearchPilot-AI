"""
Tests for POST /api/v1/documents/upload.

Task 3B Checkpoint 3's first HTTP-level tests for documents —
get_current_user's first real route consumer, exercised end-to-end
via the httpx client fixture.

Deployment milestone: storage is now Cloudflare R2, not local disk.
The shared, autouse `_mock_r2_storage` fixture in conftest.py already
provides a fresh in-memory fake R2 backend for every test in this
file — no per-file storage isolation fixture is needed anymore. Tests
that need to inspect "what got stored" use that fixture's yielded
dict (a plain `{object_key: bytes}` mapping) instead of walking a
local upload_dir with Path.glob() (the old, pre-R2 approach).
"""
import asyncio
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import settings


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


async def test_upload_actually_writes_object_to_r2(client: AsyncClient, _mock_r2_storage):
    headers = await _auth_headers(client, email="disk@example.com", username="diskuser")
    content = b"content that must actually exist in storage afterward"

    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", content, "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 201

    # _mock_r2_storage is conftest.py's shared fake-R2 store, keyed by
    # the object key (storage_path) — exactly one object was written,
    # with the exact uploaded bytes, matching the fake client's
    # put_object() contract.
    assert len(_mock_r2_storage) == 1
    stored_bytes = next(iter(_mock_r2_storage.values()))
    assert stored_bytes == content


# --- validation failures ---


async def test_upload_rejects_disallowed_extension(client: AsyncClient):
    headers = await _auth_headers(client, email="exe@example.com", username="exeuser")
    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("virus.exe", b"malicious", "application/x-msdownload")},
        headers=headers,
    )
    assert response.status_code == 422


async def test_upload_rejects_oversized_file(
    client: AsyncClient, monkeypatch, _mock_r2_storage
):
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)  # 0MB -> anything nonempty is "too large"
    headers = await _auth_headers(client, email="big@example.com", username="biguser")

    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"just a few bytes over a 0MB cap", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 413
    # Nothing should have been written — the size check happens before storage_service is called.
    assert _mock_r2_storage == {}


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


# --- GET /documents/{document_id}/file (Document Management CRUD, Checkpoint 3: download) ---


async def test_download_document_returns_owner_their_file(client: AsyncClient):
    headers = await _auth_headers(client, email="downloadowner@example.com", username="downloadowner")
    content = b"%PDF-1.4 the actual file contents"
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("report.pdf", content, "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    response = await client.get(f"/api/v1/documents/{document_id}/file", headers=headers)

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "application/pdf"
    assert "report.pdf" in response.headers.get("content-disposition", "")


async def test_download_document_requires_authentication(client: AsyncClient):
    response = await client.get(f"/api/v1/documents/{uuid.uuid4()}/file")
    assert response.status_code == 401


async def test_download_document_nonexistent_returns_404(client: AsyncClient):
    headers = await _auth_headers(client, email="downloadmissing@example.com", username="downloadmissing")
    response = await client.get(f"/api/v1/documents/{uuid.uuid4()}/file", headers=headers)
    assert response.status_code == 404


async def test_download_document_cannot_retrieve_another_users_file(client: AsyncClient):
    headers_a = await _auth_headers(client, email="downloada@example.com", username="downloada")
    headers_b = await _auth_headers(client, email="downloadb@example.com", username="downloadb")

    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("private.pdf", b"private content", "application/pdf")},
        headers=headers_a,
    )
    document_id = upload.json()["id"]

    response = await client.get(f"/api/v1/documents/{document_id}/file", headers=headers_b)
    assert response.status_code == 404


async def test_download_document_wrong_owner_indistinguishable_from_nonexistent(client: AsyncClient):
    headers_a = await _auth_headers(client, email="downloadindista@example.com", username="downloadindista")
    headers_b = await _auth_headers(client, email="downloadindistb@example.com", username="downloadindistb")

    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("owned.pdf", b"owned", "application/pdf")},
        headers=headers_a,
    )
    owned_id = upload.json()["id"]

    wrong_owner_response = await client.get(f"/api/v1/documents/{owned_id}/file", headers=headers_b)
    nonexistent_response = await client.get(f"/api/v1/documents/{uuid.uuid4()}/file", headers=headers_b)

    assert wrong_owner_response.status_code == nonexistent_response.status_code == 404
    assert wrong_owner_response.json() == nonexistent_response.json()


async def test_download_document_invalid_id_format_rejected(client: AsyncClient):
    headers = await _auth_headers(client, email="downloadbadid@example.com", username="downloadbadid")
    response = await client.get("/api/v1/documents/not-a-uuid/file", headers=headers)
    assert response.status_code == 422


async def test_download_document_does_not_expose_internal_paths_on_success(
    client: AsyncClient, _mock_r2_storage
):
    headers = await _auth_headers(client, email="downloadleak@example.com", username="downloadleak")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("paper.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    response = await client.get(f"/api/v1/documents/{document_id}/file", headers=headers)

    # No response header may contain the R2 object key (the internal,
    # UUID-based storage identifier) or any R2 configuration detail —
    # only the original filename should appear (in Content-Disposition).
    content_disposition = response.headers.get("content-disposition", "")
    assert "paper.pdf" in content_disposition
    object_key = next(iter(_mock_r2_storage.keys()))
    for header_value in response.headers.values():
        assert object_key not in header_value
        assert settings.r2_bucket_name not in header_value or settings.r2_bucket_name == ""


async def test_download_document_missing_underlying_file_returns_server_error(
    client: AsyncClient, _mock_r2_storage
):
    headers = await _auth_headers(client, email="downloadmissingfile@example.com", username="downloadmissingfile")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("willvanish.pdf", b"soon gone", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    # Simulate DB/storage drift: the row survives, but the object
    # underneath it is gone (e.g. manually deleted from the bucket,
    # bucket reset) — remove it directly from the fake R2 store.
    assert len(_mock_r2_storage) == 1
    _mock_r2_storage.clear()

    response = await client.get(f"/api/v1/documents/{document_id}/file", headers=headers)

    assert response.status_code == 500
    # The 500 body must not leak internal storage details (bucket name, object key).
    body = response.json()
    assert body["detail"] == "The stored file for this document could not be found."


async def test_upload_list_detail_still_work_after_download_endpoint_added(client: AsyncClient):
    """Regression check: adding GET /{document_id}/file must not change upload/list/detail."""
    headers = await _auth_headers(client, email="downloadregression@example.com", username="downloadregression")

    upload_response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("regress2.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    assert upload_response.status_code == 201
    document_id = upload_response.json()["id"]

    list_response = await client.get("/api/v1/documents", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    detail_response = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == document_id


# --- DELETE /documents/{document_id} (Document Management CRUD, Checkpoint 4: delete) ---


async def test_delete_document_owner_success(client: AsyncClient):
    headers = await _auth_headers(client, email="deleteowner@example.com", username="deleteowner")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("todelete.pdf", b"delete me", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    response = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    assert response.status_code == 204
    assert response.content == b""


async def test_delete_document_removes_it_from_detail(client: AsyncClient):
    headers = await _auth_headers(client, email="deletedetail@example.com", username="deletedetail")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("gone.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    await client.delete(f"/api/v1/documents/{document_id}", headers=headers)
    detail_response = await client.get(f"/api/v1/documents/{document_id}", headers=headers)

    assert detail_response.status_code == 404


async def test_delete_document_removes_it_from_list(client: AsyncClient):
    headers = await _auth_headers(client, email="deletelist@example.com", username="deletelist")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("removeme.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    await client.delete(f"/api/v1/documents/{document_id}", headers=headers)
    list_response = await client.get("/api/v1/documents", headers=headers)

    assert list_response.status_code == 200
    assert list_response.json() == []


async def test_delete_document_removes_physical_file(
    client: AsyncClient, _mock_r2_storage
):
    headers = await _auth_headers(client, email="deletefile@example.com", username="deletefile")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("physical.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    assert len(_mock_r2_storage) == 1

    await client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    assert len(_mock_r2_storage) == 0


async def test_delete_document_requires_authentication(client: AsyncClient):
    response = await client.delete(f"/api/v1/documents/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_delete_document_nonexistent_returns_404(client: AsyncClient):
    headers = await _auth_headers(client, email="deletemissing@example.com", username="deletemissing")
    response = await client.delete(f"/api/v1/documents/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_delete_document_cannot_delete_another_users_document(client: AsyncClient):
    headers_a = await _auth_headers(client, email="deletea@example.com", username="deletea")
    headers_b = await _auth_headers(client, email="deleteb@example.com", username="deleteb")

    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("a_protected.pdf", b"protected", "application/pdf")},
        headers=headers_a,
    )
    document_id = upload.json()["id"]

    response = await client.delete(f"/api/v1/documents/{document_id}", headers=headers_b)
    assert response.status_code == 404

    # Confirm it wasn't actually deleted — user A can still see it.
    still_there = await client.get(f"/api/v1/documents/{document_id}", headers=headers_a)
    assert still_there.status_code == 200


async def test_delete_document_wrong_owner_indistinguishable_from_nonexistent(client: AsyncClient):
    headers_a = await _auth_headers(client, email="deleteindista@example.com", username="deleteindista")
    headers_b = await _auth_headers(client, email="deleteindistb@example.com", username="deleteindistb")

    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("owned.pdf", b"owned", "application/pdf")},
        headers=headers_a,
    )
    owned_id = upload.json()["id"]

    wrong_owner_response = await client.delete(f"/api/v1/documents/{owned_id}", headers=headers_b)
    nonexistent_response = await client.delete(f"/api/v1/documents/{uuid.uuid4()}", headers=headers_b)

    assert wrong_owner_response.status_code == nonexistent_response.status_code == 404
    assert wrong_owner_response.json() == nonexistent_response.json()


async def test_delete_document_invalid_id_format_rejected(client: AsyncClient):
    headers = await _auth_headers(client, email="deletebadid@example.com", username="deletebadid")
    response = await client.delete("/api/v1/documents/not-a-uuid", headers=headers)
    assert response.status_code == 422


async def test_delete_document_with_already_missing_file_still_deletes_row(
    client: AsyncClient, _mock_r2_storage
):
    """
    storage_service.delete_file() is already idempotent — deleting a
    document whose underlying object was already removed (e.g. manual
    deletion, bucket reset) must still succeed in removing the stale
    DB row, per that existing semantics.
    """
    headers = await _auth_headers(client, email="deletemissingfile@example.com", username="deletemissingfile")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("willbegone.pdf", b"soon gone", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    assert len(_mock_r2_storage) == 1
    _mock_r2_storage.clear()  # simulate the object already being gone

    response = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)
    assert response.status_code == 204

    detail_response = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
    assert detail_response.status_code == 404


async def test_delete_document_does_not_affect_other_users_documents(client: AsyncClient):
    headers_a = await _auth_headers(client, email="deleteisoa@example.com", username="deleteisoa")
    headers_b = await _auth_headers(client, email="deleteisob@example.com", username="deleteisob")

    upload_a = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("a_doc.pdf", b"a content", "application/pdf")},
        headers=headers_a,
    )
    upload_b = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("b_doc.pdf", b"b content", "application/pdf")},
        headers=headers_b,
    )
    document_id_a = upload_a.json()["id"]
    document_id_b = upload_b.json()["id"]

    await client.delete(f"/api/v1/documents/{document_id_a}", headers=headers_a)

    # User B's document must be completely unaffected.
    response_b = await client.get(f"/api/v1/documents/{document_id_b}", headers=headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["id"] == document_id_b


async def test_delete_document_repeated_delete_returns_404_not_204(client: AsyncClient):
    """
    A second DELETE of an already-deleted document is a 404, not
    another 204 — the document genuinely no longer exists, so "not
    found" is the accurate response rather than a false success.
    """
    headers = await _auth_headers(client, email="deleterepeat@example.com", username="deleterepeat")
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("onceonly.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    document_id = upload.json()["id"]

    first_delete = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)
    second_delete = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    assert first_delete.status_code == 204
    assert second_delete.status_code == 404


async def test_upload_list_detail_download_still_work_after_delete_endpoint_added(client: AsyncClient):
    """Regression check: adding DELETE must not change upload/list/detail/download."""
    headers = await _auth_headers(client, email="deleteregression@example.com", username="deleteregression")

    upload_response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("stillworks.pdf", b"content", "application/pdf")},
        headers=headers,
    )
    assert upload_response.status_code == 201
    document_id = upload_response.json()["id"]

    list_response = await client.get("/api/v1/documents", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    detail_response = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
    assert detail_response.status_code == 200

    download_response = await client.get(f"/api/v1/documents/{document_id}/file", headers=headers)
    assert download_response.status_code == 200
    assert download_response.content == b"content"
