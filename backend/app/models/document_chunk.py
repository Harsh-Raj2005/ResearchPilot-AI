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

Document Chunks -> Embeddings milestone: adds `embedding`
(Vector(1536), OpenAI text-embedding-3-small's default dimensionality)
as a NOT NULL column. This is deliberately NOT nullable: the
processing pipeline computes every chunk's embedding *before*
constructing its DocumentChunk row (see chunk_service._replace_chunks
and document_processing_service.process_document), so a DocumentChunk
never exists — not even transiently, mid-transaction — without a
valid embedding. NOT NULL lets the database itself enforce that
invariant rather than relying on application discipline alone. No
vector index yet (deferred to whichever future milestone actually
needs multi-document/large-scale retrieval — premature at Phase 1's
single-document scale) and no embedding metadata (model/version/
status/embedded_at) — only one provider/model is in use, and a future
model change is a deliberate future migration, not designed
speculatively now.
"""
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

EMBEDDING_DIMENSIONS = 1536


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

    # A chunk's text content.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # OpenAI text-embedding-3-small, 1536 dimensions, NOT NULL — see
    # this module's own docstring for why nullable=False is safe and
    # correct given the processing order.
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentChunk id={self.id} document_text_id={self.document_text_id} "
            f"chunk_index={self.chunk_index}>"
        )
