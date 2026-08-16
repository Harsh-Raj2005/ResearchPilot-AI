"""
Tests for app.services.storage_service (Cloudflare R2 / S3-compatible
object storage).

Uses conftest.py's shared, autouse `_mock_r2_storage` fixture — a fake
in-memory S3 client monkeypatched onto
storage_service._get_client_context() — for every test in this file.
No real network call to R2 is ever made, and no real AWS/Cloudflare
credentials are required to run this file.
"""
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app.core.config import settings
from app.services import storage_service


# --- validate_extension (pure function, unchanged by the R2 migration) ---


def test_validate_extension_accepts_allowed_types():
    assert storage_service.validate_extension("paper.pdf") == ".pdf"
    assert storage_service.validate_extension("notes.docx") == ".docx"
    assert storage_service.validate_extension("readme.txt") == ".txt"


def test_validate_extension_is_case_insensitive():
    assert storage_service.validate_extension("PAPER.PDF") == ".pdf"


def test_validate_extension_rejects_disallowed_type():
    with pytest.raises(storage_service.UnsupportedFileTypeError):
        storage_service.validate_extension("malware.exe")


def test_validate_extension_rejects_missing_extension():
    with pytest.raises(storage_service.UnsupportedFileTypeError):
        storage_service.validate_extension("no_extension")


# --- generate_stored_filename (pure function, unchanged) ---


def test_generate_stored_filename_preserves_extension():
    stored = storage_service.generate_stored_filename("my_paper.PDF")
    assert stored.endswith(".pdf")


def test_generate_stored_filename_is_unique_across_calls():
    first = storage_service.generate_stored_filename("same_name.pdf")
    second = storage_service.generate_stored_filename("same_name.pdf")
    assert first != second


def test_generate_stored_filename_rejects_disallowed_extension():
    with pytest.raises(storage_service.UnsupportedFileTypeError):
        storage_service.generate_stored_filename("virus.exe")


# --- save_file ---


async def test_save_file_uploads_and_returns_correct_metadata(_mock_r2_storage):
    content = b"hello world, this is a test document"
    saved = await storage_service.save_file(
        content=content, original_filename="notes.txt", content_type="text/plain"
    )

    assert saved.original_filename == "notes.txt"
    assert saved.stored_filename.endswith(".txt")
    assert saved.stored_filename != "notes.txt"  # never the original filename
    assert saved.content_type == "text/plain"
    assert saved.file_size_bytes == len(content)
    assert saved.storage_path == saved.stored_filename  # the object key
    assert _mock_r2_storage[saved.storage_path] == content


async def test_save_file_preserves_original_filename_separately_from_stored_filename(
    _mock_r2_storage,
):
    saved = await storage_service.save_file(
        content=b"data", original_filename="my_research_paper_final_v2.pdf",
        content_type="application/pdf",
    )
    assert saved.original_filename == "my_research_paper_final_v2.pdf"
    assert saved.stored_filename != saved.original_filename


async def test_save_file_two_uploads_with_same_original_filename_do_not_collide(
    _mock_r2_storage,
):
    first = await storage_service.save_file(
        content=b"version one", original_filename="draft.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    second = await storage_service.save_file(
        content=b"version two", original_filename="draft.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert first.stored_filename != second.stored_filename
    assert _mock_r2_storage[first.storage_path] == b"version one"
    assert _mock_r2_storage[second.storage_path] == b"version two"


async def test_save_file_rejects_disallowed_extension_without_uploading_anything(
    _mock_r2_storage,
):
    with pytest.raises(storage_service.UnsupportedFileTypeError):
        await storage_service.save_file(
            content=b"malicious", original_filename="virus.exe",
            content_type="application/x-msdownload",
        )
    # Nothing should have been uploaded — validation happens before
    # any R2 call is made.
    assert _mock_r2_storage == {}


async def test_save_file_wraps_provider_error_as_storage_error(monkeypatch):
    class _BoomClient:
        async def put_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "InternalError"}}, "PutObject")

    class _BoomContext:
        async def __aenter__(self):
            return _BoomClient()

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(storage_service, "_get_client_context", lambda: _BoomContext())

    with pytest.raises(storage_service.StorageError):
        await storage_service.save_file(
            content=b"data", original_filename="paper.pdf", content_type="application/pdf"
        )


async def test_save_file_error_does_not_leak_credentials(monkeypatch):
    class _BoomClient:
        async def put_object(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "secret-access-key-xyz-leaked"}},
                "PutObject",
            )

    class _BoomContext:
        async def __aenter__(self):
            return _BoomClient()

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(storage_service, "_get_client_context", lambda: _BoomContext())

    with pytest.raises(storage_service.StorageError) as exc_info:
        await storage_service.save_file(
            content=b"data", original_filename="paper.pdf", content_type="application/pdf"
        )
    assert "secret-access-key-xyz-leaked" not in str(exc_info.value)


# --- delete_file ---


async def test_delete_file_removes_an_existing_object(_mock_r2_storage):
    saved = await storage_service.save_file(
        content=b"to be deleted", original_filename="temp.txt", content_type="text/plain"
    )
    assert saved.storage_path in _mock_r2_storage

    await storage_service.delete_file(saved.storage_path)

    assert saved.storage_path not in _mock_r2_storage


async def test_delete_file_is_idempotent_for_a_missing_object(_mock_r2_storage):
    # Must not raise, even though the key was never uploaded.
    await storage_service.delete_file("never-existed.pdf")


async def test_delete_file_wraps_provider_error_as_storage_error(monkeypatch):
    class _BoomClient:
        async def delete_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "InternalError"}}, "DeleteObject")

    class _BoomContext:
        async def __aenter__(self):
            return _BoomClient()

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(storage_service, "_get_client_context", lambda: _BoomContext())

    with pytest.raises(storage_service.StorageError):
        await storage_service.delete_file("some-key.pdf")


# --- get_file_bytes ---


async def test_get_file_bytes_returns_uploaded_content(_mock_r2_storage):
    content = b"the actual file bytes"
    saved = await storage_service.save_file(
        content=content, original_filename="paper.pdf", content_type="application/pdf"
    )

    result = await storage_service.get_file_bytes(saved.storage_path)

    assert result == content


async def test_get_file_bytes_missing_object_raises_stored_file_not_found(_mock_r2_storage):
    with pytest.raises(storage_service.StoredFileNotFoundError):
        await storage_service.get_file_bytes("does-not-exist.pdf")


async def test_get_file_bytes_other_provider_error_raises_storage_error(monkeypatch):
    class _BoomClient:
        async def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "InternalError"}}, "GetObject")

    class _BoomContext:
        async def __aenter__(self):
            return _BoomClient()

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(storage_service, "_get_client_context", lambda: _BoomContext())

    with pytest.raises(storage_service.StorageError):
        await storage_service.get_file_bytes("some-key.pdf")


# --- get_file_path ---


async def test_get_file_path_downloads_to_a_real_temp_file(_mock_r2_storage):
    content = b"%PDF-1.4 fake pdf bytes for parsing"
    saved = await storage_service.save_file(
        content=content, original_filename="paper.pdf", content_type="application/pdf"
    )

    result_path = await storage_service.get_file_path(saved.storage_path)

    try:
        assert result_path.is_file()
        assert result_path.read_bytes() == content
        assert result_path.suffix == ".pdf"
    finally:
        result_path.unlink(missing_ok=True)


async def test_get_file_path_missing_object_raises_stored_file_not_found(_mock_r2_storage):
    with pytest.raises(storage_service.StoredFileNotFoundError):
        await storage_service.get_file_path("does-not-exist.pdf")


async def test_get_file_path_does_not_delete_the_temp_file_itself(_mock_r2_storage):
    """
    get_file_path() is documented as the CALLER's responsibility to
    clean up (see document_text_service._upsert_document_text()'s own
    finally-block cleanup) — it must not delete the file itself before
    returning.
    """
    saved = await storage_service.save_file(
        content=b"data", original_filename="paper.pdf", content_type="application/pdf"
    )

    result_path = await storage_service.get_file_path(saved.storage_path)

    assert result_path.exists()
    result_path.unlink()  # test's own cleanup


# --- configuration values used correctly ---


async def test_save_file_uses_configured_bucket_name(monkeypatch):
    monkeypatch.setattr(settings, "r2_bucket_name", "my-configured-bucket")
    calls = []

    class _RecordingClient:
        async def put_object(self, *, Bucket, Key, Body, ContentType=None):
            calls.append(Bucket)

    class _RecordingContext:
        async def __aenter__(self):
            return _RecordingClient()

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(storage_service, "_get_client_context", lambda: _RecordingContext())

    await storage_service.save_file(
        content=b"data", original_filename="paper.pdf", content_type="application/pdf"
    )

    assert calls == ["my-configured-bucket"]
