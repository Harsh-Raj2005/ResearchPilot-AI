"""
ChatSession model.

Chat Persistence milestone: a persisted conversation about one
document. Deliberately no user_id column — mirrors the exact
precedent DocumentChunk already sets by not carrying a redundant
document_id alongside document_text_id. Ownership is reachable via
session -> document -> document.user_id, a single path, not
duplicated. Every service function that needs "sessions for this
user" (or ownership verification) joins through Document, exactly
like retrieval_service.retrieve_similar_chunks() already joins
DocumentChunk -> DocumentText to reach Document.

No title column: a speculative "auto-generate a session title"
feature is not part of this milestone's scope; if wanted later, that
is an additive column, not a blocker now.
"""
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class ChatSession(BaseModel):
    __tablename__ = "chat_sessions"

    # Deliberately no relationship() — nothing needs ORM navigation
    # yet; every caller queries explicitly, the same pattern already
    # used everywhere else in this codebase. Not unique: one Document
    # legitimately has many ChatSession rows.
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<ChatSession id={self.id} document_id={self.document_id}>"
