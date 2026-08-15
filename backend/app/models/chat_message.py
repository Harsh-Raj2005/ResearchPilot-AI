"""
ChatMessage model.

Chat Persistence milestone: one turn (user question or assistant
answer) within a ChatSession.

`sequence_number` (INTEGER, NOT NULL) is a deliberate addition, not a
speculative one: `created_at` alone is not a safe ordering guarantee
for a user-message -> assistant-response pair written in quick
succession within the same request, where both could plausibly land
within the same DB timestamp resolution window. Getting message order
wrong is a correctness bug a later migration can't cheaply fix without
a backfill, so this is added now rather than deferred. Assigned by
chat_session_service (0-based, monotonically increasing per
chat_session_id) — not database-generated, since the correct next
value depends on already-persisted rows for this session.

`role` is a plain string column ("user" or "assistant"), not a
separate lookup table — two fixed values don't justify a full
reference table, matching this project's "no abstraction without a
real second consumer" principle.

Composite UniqueConstraint(chat_session_id, sequence_number) mirrors
the exact pattern DocumentChunk already establishes for
(document_text_id, chunk_index) — the database enforces "no two
messages claim the same position in the same session" structurally,
not just by application convention.
"""
import uuid

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class ChatMessage(BaseModel):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint(
            "chat_session_id", "sequence_number", name="uq_chat_messages_session_sequence"
        ),
    )

    chat_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # "user" or "assistant" — plain string, not an Enum type, to avoid
    # an Alembic-managed Postgres ENUM's own migration friction for
    # two fixed values with no third value currently anticipated.
    role: Mapped[str] = mapped_column(Text, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # 0-based, assigned by chat_session_service, monotonically
    # increasing per chat_session_id. See this module's own docstring
    # for why this exists instead of relying on created_at ordering.
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ChatMessage id={self.id} chat_session_id={self.chat_session_id} "
            f"role={self.role} sequence_number={self.sequence_number}>"
        )
