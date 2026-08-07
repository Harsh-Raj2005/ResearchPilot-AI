"""
Tests for app.services.storage_service.

Pure file I/O, no DB — no db_session/client fixtures needed. Each
test redirects settings.upload_dir to a pytest tmp_path so nothing
ever touches the real dev storage/uploads/ folder.
"""
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import storage_service


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    """Redirect settings.upload_dir to a fresh temp directory per test."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    yield tmp_path


# --- validate_extension ---


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


# --- generate_stored_filename ---


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


# --- ensure_upload_dir_exists ---


def test_ensure_upload_dir_exists_creates_missing_directory():
    upload_dir = Path(settings.upload_dir)
    assert not upload_dir.exists()

    result = storage_service.ensure_upload_dir_exists()

    assert upload_dir.exists()
    assert upload_dir.is_dir()
    assert result == upload_dir


def test_ensure_upload_dir_exists_is_idempotent():
    storage_service.ensure_upload_dir_exists()
    # Calling it again with the directory already present must not raise.
    storage_service.ensure_upload_dir_exists()
    assert Path(settings.upload_dir).exists()


# --- save_file ---


def test_save_file_creates_directory_automatically():
    upload_dir = Path(settings.upload_dir)
    assert not upload_dir.exists()

    storage_service.save_file(
        content=b"%PDF-1.4 fake pdf bytes",
        original_filename="thesis.pdf",
        content_type="application/pdf",
    )

    assert upload_dir.exists()


def test_save_file_writes_content_and_returns_correct_metadata():
    content = b"hello world, this is a test document"
    saved = storage_service.save_file(
        content=content, original_filename="notes.txt", content_type="text/plain"
    )

    assert saved.original_filename == "notes.txt"
    assert saved.stored_filename.endswith(".txt")
    assert saved.stored_filename != "notes.txt"  # never the original filename
    assert saved.content_type == "text/plain"
    assert saved.file_size_bytes == len(content)
    assert Path(saved.storage_path).exists()
    assert Path(saved.storage_path).read_bytes() == content


def test_save_file_preserves_original_filename_separately_from_stored_filename():
    saved = storage_service.save_file(
        content=b"data", original_filename="my_research_paper_final_v2.pdf",
        content_type="application/pdf",
    )
    assert saved.original_filename == "my_research_paper_final_v2.pdf"
    assert saved.stored_filename != saved.original_filename


def test_save_file_two_uploads_with_same_original_filename_do_not_collide():
    first = storage_service.save_file(
        content=b"version one", original_filename="draft.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    second = storage_service.save_file(
        content=b"version two", original_filename="draft.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert first.stored_filename != second.stored_filename
    assert Path(first.storage_path).read_bytes() == b"version one"
    assert Path(second.storage_path).read_bytes() == b"version two"


def test_save_file_rejects_disallowed_extension_without_writing_anything():
    upload_dir = Path(settings.upload_dir)
    with pytest.raises(storage_service.UnsupportedFileTypeError):
        storage_service.save_file(
            content=b"malicious", original_filename="virus.exe", content_type="application/x-msdownload"
        )
    # Nothing should have been written — not even the directory, since
    # validation happens before ensure_upload_dir_exists() is called.
    assert not upload_dir.exists()


def test_save_file_wraps_filesystem_error_as_storage_error(monkeypatch):
    def _boom(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", _boom)

    with pytest.raises(storage_service.StorageError):
        storage_service.save_file(
            content=b"data", original_filename="paper.pdf", content_type="application/pdf"
        )


# --- delete_file ---


def test_delete_file_removes_an_existing_file():
    saved = storage_service.save_file(
        content=b"to be deleted", original_filename="temp.txt", content_type="text/plain"
    )
    assert Path(saved.storage_path).exists()

    storage_service.delete_file(saved.storage_path)

    assert not Path(saved.storage_path).exists()


def test_delete_file_is_idempotent_for_a_missing_file(tmp_path):
    nonexistent = tmp_path / "already_gone.pdf"
    # Must not raise, even though the file was never created.
    storage_service.delete_file(str(nonexistent))


def test_delete_file_wraps_filesystem_error_as_storage_error(monkeypatch):
    def _boom(self, missing_ok=False):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", _boom)

    with pytest.raises(storage_service.StorageError):
        storage_service.delete_file("/some/path.pdf")
