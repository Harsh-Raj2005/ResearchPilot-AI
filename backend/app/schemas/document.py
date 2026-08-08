"""
Document request/response schemas.

Task 3B Checkpoint 3 scope: response shape for the upload endpoint
only. List/detail schemas can reuse this same DocumentResponse once
those endpoints exist (later checkpoint) — no new schema needed then.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    """
    Safe response shape for a document — deliberately does not
    include `stored_filename` or `storage_path`, which are internal
    storage details the client has no use for and shouldn't see
    (mirrors UserPublic never including hashed_password).
    """

    id: uuid.UUID
    original_filename: str
    content_type: str
    file_size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}
