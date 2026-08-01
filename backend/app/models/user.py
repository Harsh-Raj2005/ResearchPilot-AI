"""
User model.

Task 2.1 scope: schema only. No password hashing, no auth logic, no
routes — those arrive in the auth task and will live in
app/services/auth_service.py and app/api/auth.py, not here. This file
should only ever describe the shape of the `users` table.
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class User(BaseModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    username: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
