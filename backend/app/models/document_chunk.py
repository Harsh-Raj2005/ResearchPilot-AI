"""
DocumentChunk model.

Document Chunking milestone scope: schema only, populated by
document_processing_service.process_document() (new this milestone).

Chunks belong to a DocumentText, not directly to a Document — a chunk
is derived from *extracted text*, not from the source file itself.
When a document is reprocessed and its DocumentText.content changes,
chunks are regenerated from that new content, so tying the FK to
document_text_id (rather than duplicating a document_id FK) avoids a
redundant, independently-driftable path back to the parent document;
the existing chunk -> document_text -> document chain is the single
source of truth. This mirrors the same reasoning DocumentText itself
already applies (no relationship(), no redundant FKs — see that
model's own docstring).

One DocumentText -> zero or many DocumentChunk rows (unlike
DocumentText's own strict 1:0..1 relationship to Document). Zero
chunks is a valid, expected state for empty extracted text.
"""
import uuid

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class DocumentChunk(BaseModel):
    __tablename__ = "document_chunks"
    __table_args__ = (
        # Enforces "no two chunks claim the same position for the
        # same text" at the database level, not just by application
        # convention — mirrors DocumentText.document_id's own
        # unique=True enforcing its 1:0..1 invariant structurally.
        UniqueConstraint("document_text_id", "chunk_index", name="uq_document_chunks_text_index"),
    )

    # Deliberately no relationship() on either this model or
    # DocumentText — nothing needs ORM navigation yet; every caller
    # queries explicitly (select(DocumentChunk).where(...)), the same
    # pattern already used everywhere else in this codebase.
    #
    # Not unique (unlike DocumentText.document_id): one DocumentText
    # legitimately has many DocumentChunk rows.
    document_text_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_texts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # 0-based ordering within the document, in original document
    # order. Assigned by chunk_service.chunk_text()'s emission order.
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # A chunk's text content. No embedding/vector columns — those
    # belong to a later, separate milestone (see chunk_service.py's
    # own docstring for the full rationale).
    content: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<DocumentChunk id={self.id} document_text_id={self.document_text_id} "
            f"chunk_index={self.chunk_index}>"
        )
