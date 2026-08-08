"""
Storage service.

Task 3B Checkpoint 2 scope: local-disk file I/O only. No database
access, no HTTP/UploadFile knowledge, no upload endpoint — this
module knows how to save, delete, and (as of Document Management
Checkpoint 3) locate bytes on disk, and nothing else. Routers call
document_service, which calls this; this module never talks to the
DB or raises HTTPException directly, matching the project's
service-layer conventions.

Deliberately decoupled from FastAPI's UploadFile: save_file() takes
raw bytes + a filename + a content type, so this module is testable
and reusable independent of the web framework. get_file_path()
returns a Path, not file content — FastAPI's FileResponse streams
directly from that path, so this module never loads a downloaded
file's bytes into memory either.
"""
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


class UnsupportedFileTypeError(Exception):
    """Raised when a filename's extension isn't in the allowed list."""


class StorageError(Exception):
    """
    Raised when a filesystem operation fails for a reason outside the
    caller's control (disk full, permission denied, etc.) — wraps the
    underlying OSError so callers deal with one domain exception
    rather than needing to know every OSError subtype that might occur.
    """


class StoredFileNotFoundError(Exception):
    """
    Raised when a storage_path that should point to a real file
    doesn't — e.g. a Document row survives but the file was deleted
    outside the app, or a volume was reset. A legitimate DB/filesystem
    drift, not a programming error, so it's its own exception rather
    than an assertion or a bare FileNotFoundError leaking out.
    """


@dataclass(frozen=True)
class SavedFile:
    """
    Everything the (later) document service needs to persist a
    Document row after a successful save — field names deliberately
    match the Document model's columns 1:1 so a future caller can
    construct a Document from this without renaming anything.
    """

    stored_filename: str
    storage_path: str
    original_filename: str
    content_type: str
    file_size_bytes: int


def _upload_dir_path() -> Path:
    return Path(settings.upload_dir)


def ensure_upload_dir_exists() -> Path:
    """
    Create the upload directory (and any missing parents) if it
    doesn't already exist. Idempotent — safe to call on every save.
    """
    upload_dir = _upload_dir_path()
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"Could not create upload directory {upload_dir}: {exc}") from exc
    return upload_dir


def validate_extension(original_filename: str) -> str:
    """
    Returns the lowercased extension (e.g. ".pdf") if it's in the
    allowed list, otherwise raises UnsupportedFileTypeError. A missing
    extension is treated the same as a disallowed one.

    Extension-only validation for Phase 1, deliberately — see
    PROJECT_CONTEXT.md Section 11 #18 for why python-magic content
    sniffing is deferred rather than added here.
    """
    extension = Path(original_filename).suffix.lower()
    if not extension or extension not in settings.allowed_upload_extensions_list:
        allowed = ", ".join(settings.allowed_upload_extensions_list)
        raise UnsupportedFileTypeError(
            f"File type {extension or '(none)'!r} is not allowed. Allowed types: {allowed}"
        )
    return extension


def generate_stored_filename(original_filename: str) -> str:
    """
    A UUID4 plus the original file's (already-validated) extension —
    never derived from the original filename itself, which avoids any
    path-traversal risk from user-controlled input and guarantees no
    collisions across users/uploads.
    """
    extension = validate_extension(original_filename)
    return f"{uuid.uuid4()}{extension}"


def save_file(*, content: bytes, original_filename: str, content_type: str) -> SavedFile:
    """
    Validate, generate a stored filename, and write `content` to disk.

    Raises UnsupportedFileTypeError if the extension isn't allowed
    (before anything is written), or StorageError if the write itself
    fails for a filesystem reason.
    """
    stored_filename = generate_stored_filename(original_filename)
    upload_dir = ensure_upload_dir_exists()
    destination = upload_dir / stored_filename

    try:
        destination.write_bytes(content)
    except OSError as exc:
        raise StorageError(f"Could not save file to {destination}: {exc}") from exc

    return SavedFile(
        stored_filename=stored_filename,
        storage_path=str(destination),
        original_filename=original_filename,
        content_type=content_type,
        file_size_bytes=len(content),
    )


def delete_file(storage_path: str) -> None:
    """
    Delete a previously-saved file by its storage_path.

    Idempotent: a file that's already gone is treated as success, not
    an error — a caller retrying a delete (or cleaning up after a
    partially-failed operation) shouldn't have to special-case "already
    deleted". Any other filesystem failure (e.g. permission denied)
    raises StorageError.
    """
    try:
        Path(storage_path).unlink(missing_ok=True)
    except OSError as exc:
        raise StorageError(f"Could not delete file {storage_path}: {exc}") from exc


def get_file_path(storage_path: str) -> Path:
    """
    Return the Path to a previously-saved file, after verifying it
    actually exists on disk.

    Raises StoredFileNotFoundError if storage_path doesn't correspond
    to a real file — callers (document_service) must handle this
    explicitly rather than let a confusing low-level FileNotFoundError
    surface from deep inside a FastAPI FileResponse. Deliberately just
    a path lookup, not a read: FastAPI's FileResponse streams from the
    path itself, so this function never loads file content into memory.
    """
    path = Path(storage_path)
    if not path.is_file():
        raise StoredFileNotFoundError(f"Stored file not found: {storage_path}")
    return path
