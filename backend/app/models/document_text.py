"""
DocumentText model.

Document Text Extraction Checkpoint 3 scope: schema only. This table
persists the output of parse_service.extract_text(), but nothing
writes to it yet — no upload wiring, no parsing orchestration. That's
an explicit later checkpoint (see PROJECT_CONTEXT.md).

Design approved in Checkpoint 2's review: a separate table, not an
extracted_text column on Document. Document represents the file's
metadata and stored source; DocumentText represents derived
processing output. Keeping them separate means existing Document
queries (list_documents_for_user, get_document_for_user, ...) never
risk silently loading large text they don't need — SQLAlchemy's
default select(Document) loads every column on Document, so adding
a large text column there would be a real, not hypothetical, cost on
every existing list/detail call.
"""
import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class DocumentText(BaseModel):
    __tablename__ = "document_texts"

    # One Document -> zero or one DocumentText, enforced at the DB
    # level via unique=True, not just assumed by application code.
    # Deliberately no relationship() on either this model or Document
    # — nothing needs ORM navigation yet; a future service queries
    # `select(DocumentText).where(DocumentText.document_id == ...)`
    # explicitly, the same pattern already used everywhere else in
    # this codebase (see Document.user_id's identical reasoning).
    #
    # No versioning (no version/parser_version/is_current column): if
    # real multi-version history is ever needed, that's a deliberate
    # future migration that relaxes this unique constraint — not
    # designed for speculatively here.
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    # parse_service.extract_text()'s output, verbatim. Must allow ""
    # — a valid PDF with no extractable text is a legitimate outcome,
    # not a failure, and is NOT the same thing as "no DocumentText
    # row exists" (which, at this stage, is the only signal for
    # "not parsed" / "parsing failed" — no status column yet).
    content: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<DocumentText id={self.id} document_id={self.document_id}>"
