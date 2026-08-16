"""
Storage service.

Deployment milestone: rewritten from local-disk file I/O to
Cloudflare R2 (S3-compatible object storage). Local disk on a
Render/Railway-style container is not durable across redeploys or
restarts — an uploaded PDF (and the ability to reprocess it) would
silently vanish on every deploy. R2 was chosen over a Render/Railway
persistent volume because it requires no per-provider volume
configuration and keeps storage fully decoupled from whichever
compute provider hosts the backend (see the Deployment Design
Report's storage-options comparison for the full rationale).

Still knows nothing about the DB or HTTP — routers call
document_service, which calls this; this module never talks to the
DB or raises HTTPException directly, matching the project's existing
service-layer conventions. Still decoupled from FastAPI's UploadFile:
save_file() takes raw bytes + a filename + a content type.

Uses aioboto3 (an async wrapper over boto3/aiobotocore), not the
synchronous boto3 directly — every other I/O path in this codebase
(SQLAlchemy, the OpenAI SDK) is genuinely async, and a blocking
synchronous network call to R2 inside an `async def` route handler
would block the event loop for every other concurrent request while
it ran. A client is constructed per call via `_get_client()`
(mirroring embedding_service.py's and llm_service.py's identical
"construct per call, not a module-level singleton" rationale: no
live client at import time, and tests can freely monkeypatch this one
factory function without needing to reset cached client state).

Object keys (what `storage_path` now stores) are still a UUID4 plus
the original file's validated extension — never derived from the
original filename, for the exact same path-traversal/collision
reasoning the local-disk version already established. `storage_path`
remains a plain string column on Document; its *meaning* changed
from "a local filesystem path" to "an R2 object key", but no schema
migration is needed since it was always just a string.

Two distinct read paths, not one, because their callers need
genuinely different shapes:
- get_file_path(): downloads the object to a temporary local file and
  returns its Path — used by document_text_service.py, which hands
  that Path straight to parse_service.extract_text(Path), a function
  this milestone deliberately does NOT touch (PyMuPDF's `pymupdf.open()`
  wants a real file, not bytes, and rewriting parse_service.py's own
  tested contract is out of this milestone's scope). The temp file's
  entire lifetime is contained within one call — the caller downloads,
  parses, and the temp file is deleted in a `finally` block before the
  function returns, so no temp file survives past a single parse.
- get_file_bytes(): downloads and returns the raw bytes directly — used
  by the download endpoint, which needs to build an HTTP response body
  (see app/api/documents.py's download_document(), which replaced
  FileResponse(path=...) with a plain Response(content=...) for this
  exact reason — FileResponse requires a real local path it can stream
  from disk; a plain Response does not).
"""
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import aioboto3
from botocore.exceptions import ClientError

from app.core.config import settings


class UnsupportedFileTypeError(Exception):
    """Raised when a filename's extension isn't in the allowed list."""


class StorageError(Exception):
    """
    Raised when an R2/object-storage operation fails for a reason
    outside the caller's control (network failure, credential/
    permission error, provider outage) — wraps the underlying
    botocore.exceptions.ClientError (or any other unexpected failure)
    so callers deal with one domain exception rather than needing to
    know every provider-specific exception type that might occur.
    Never includes the raw provider error text, credentials, or
    endpoint URL in its message — mirrors embedding_service.
    EmbeddingProviderError's identical "don't leak provider detail"
    discipline.
    """


class StoredFileNotFoundError(Exception):
    """
    Raised when a storage_path (object key) that should point to a
    real object doesn't — e.g. a Document row survives but the object
    was deleted outside the app, or the bucket was reset. A legitimate
    DB/storage drift, not a programming error, so it's its own
    exception rather than an assertion or a bare provider "NoSuchKey"
    error leaking out.
    """


@dataclass(frozen=True)
class SavedFile:
    """
    Everything document_service needs to persist a Document row after
    a successful save — field names deliberately match the Document
    model's columns 1:1 so a caller can construct a Document from this
    without renaming anything. `storage_path` is now an R2 object key
    (e.g. "3f2b...-c1a9.pdf"), not a filesystem path — the field name
    is unchanged since Document.storage_path's column name and meaning
    to the rest of the app ("the opaque string get_file_path()/
    get_file_bytes() need to retrieve this file") are unchanged; only
    what that string actually addresses has changed.
    """

    stored_filename: str
    storage_path: str
    original_filename: str
    content_type: str
    file_size_bytes: int


def _get_client_context():
    """
    Returns the async context manager for an R2 client — callers do
    `async with _get_client_context() as client:`. A thin factory,
    not a cached singleton, mirroring embedding_service._get_client()'s
    identical per-call-construction rationale: tests can freely
    monkeypatch this one function without needing to reset any cached
    client state, and no live client/connection exists at import time.
    """
    session = aioboto3.Session()
    return session.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
    )


def validate_extension(original_filename: str) -> str:
    """
    Returns the lowercased extension (e.g. ".pdf") if it's in the
    allowed list, otherwise raises UnsupportedFileTypeError. A missing
    extension is treated the same as a disallowed one.

    Extension-only validation for Phase 1, deliberately — see
    PROJECT_CONTEXT.md Section 11 #18 for why python-magic content
    sniffing is deferred rather than added here. Unchanged by the R2
    migration — this function never touched the filesystem or R2.
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
    collisions across users/uploads. Unchanged by the R2 migration;
    this value is now used as the R2 object key rather than a local
    filename, but the generation logic itself doesn't care which.
    """
    extension = validate_extension(original_filename)
    return f"{uuid.uuid4()}{extension}"


async def save_file(*, content: bytes, original_filename: str, content_type: str) -> SavedFile:
    """
    Validate, generate an object key, and upload `content` to R2.

    Raises UnsupportedFileTypeError if the extension isn't allowed
    (before any network call is made), or StorageError if the upload
    itself fails for a provider/network reason.
    """
    stored_filename = generate_stored_filename(original_filename)

    try:
        async with _get_client_context() as client:
            await client.put_object(
                Bucket=settings.r2_bucket_name,
                Key=stored_filename,
                Body=content,
                ContentType=content_type,
            )
    except ClientError as exc:
        raise StorageError("Could not save file to object storage.") from exc

    return SavedFile(
        stored_filename=stored_filename,
        storage_path=stored_filename,
        original_filename=original_filename,
        content_type=content_type,
        file_size_bytes=len(content),
    )


async def delete_file(storage_path: str) -> None:
    """
    Delete a previously-saved object by its storage_path (object key).

    Idempotent: R2's delete_object (like S3's) returns success even if
    the key doesn't exist — the same "a caller retrying a delete
    shouldn't have to special-case 'already deleted'" guarantee the
    local-disk version already established, now provided by the
    provider itself rather than needing `missing_ok=True`-style
    handling in this function.
    """
    try:
        async with _get_client_context() as client:
            await client.delete_object(Bucket=settings.r2_bucket_name, Key=storage_path)
    except ClientError as exc:
        raise StorageError("Could not delete file from object storage.") from exc


async def get_file_bytes(storage_path: str) -> bytes:
    """
    Download and return an object's raw bytes.

    Used by the download endpoint to build an HTTP response body
    directly (see this module's own docstring for why this is a
    separate function from get_file_path() rather than one shared
    implementation).

    Raises StoredFileNotFoundError if storage_path doesn't correspond
    to a real object, or StorageError for any other provider/network
    failure.
    """
    try:
        async with _get_client_context() as client:
            response = await client.get_object(Bucket=settings.r2_bucket_name, Key=storage_path)
            async with response["Body"] as body:
                return await body.read()
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("NoSuchKey", "404"):
            raise StoredFileNotFoundError(f"Stored file not found: {storage_path}") from exc
        raise StorageError("Could not read file from object storage.") from exc


async def get_file_path(storage_path: str) -> Path:
    """
    Download an object to a temporary local file and return its Path.

    Used by document_text_service.py, which hands the result straight
    to parse_service.extract_text(Path) — a function this milestone
    deliberately does not touch (see this module's own docstring for
    the full reasoning). The temp file is created with a matching
    suffix (so parse_service's own extension check still works
    unmodified) and is the CALLER's responsibility to delete once
    parsing is done — this function does not clean up after itself,
    since it has no way to know when the caller is finished reading
    from the returned Path.

    Raises StoredFileNotFoundError if storage_path doesn't correspond
    to a real object, or StorageError for any other provider/network
    failure. On either failure, no temp file is left behind.
    """
    suffix = Path(storage_path).suffix
    try:
        async with _get_client_context() as client:
            response = await client.get_object(Bucket=settings.r2_bucket_name, Key=storage_path)
            async with response["Body"] as body:
                content = await body.read()
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("NoSuchKey", "404"):
            raise StoredFileNotFoundError(f"Stored file not found: {storage_path}") from exc
        raise StorageError("Could not read file from object storage.") from exc

    temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        temp_file.write(content)
    finally:
        temp_file.close()
    return Path(temp_file.name)
