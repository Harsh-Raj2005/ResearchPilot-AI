"""
Shared base classes for all ORM models.

`Base` (the SQLAlchemy DeclarativeBase) already lives in app/db/session.py,
because the engine/session module needs it regardless of whether any
models exist yet. This module builds on top of it with the two things
every model repeats: a UUID primary key and created_at/updated_at
timestamps. New models inherit from `BaseModel` below instead of
`Base` directly.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TimestampMixin:
    """
    Adds created_at / updated_at columns, both database-generated.

    Using the SQL-side `func.now()` (rather than a Python-side
    `datetime.utcnow()` default) means the timestamp is always assigned
    by the database at insert/update time — correct regardless of which
    code path performs the write, and not dependent on the app server's
    clock. `onupdate=func.now()` tells SQLAlchemy to include a fresh
    `now()` in the generated UPDATE statement automatically, so callers
    never need to set `updated_at` by hand.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BaseModel(Base, TimestampMixin):
    """
    Common base for all ORM models: UUID primary key + timestamps.

    `__abstract__ = True` means this class itself creates no table —
    only concrete subclasses (e.g. `User`) do, and each gets its own
    `id`, `created_at`, and `updated_at` columns generated from here.

    Note on naming: this class is called `BaseModel`, matching the
    SQLAlchemy-community convention, but it is unrelated to Pydantic's
    `BaseModel` used in app/schemas/. The two are never imported into
    the same module unaliased, so there's no real collision risk — just
    worth knowing if you see `BaseModel` in both an ORM model file and
    a Pydantic schema file and wonder if they're the same thing. They
    are not.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
