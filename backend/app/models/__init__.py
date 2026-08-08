"""
Importing this package populates Base.metadata with every model,
which is what makes Alembic autogenerate (and any future
`Base.metadata.create_all` in tests) able to see them.

Add each new model's import here as it's created — this is the single
place Alembic's env.py (and anything else that needs the full schema)
imports from, so no model is ever "invisible" to migrations by
accident.
"""
from app.models.base import BaseModel  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.document_text import DocumentText  # noqa: F401
