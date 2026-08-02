"""
Auth request/response schemas.

Validation lives here, not in the router or service — e.g. password
length is checked at the schema boundary so invalid requests are
rejected before any business logic runs.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    # max_length here bounds character count; bcrypt's limit (enforced
    # in app/core/security.py) is on byte count, which multi-byte UTF-8
    # characters can exceed even under 72 characters. The validator
    # below checks the byte length explicitly so an over-long password
    # fails as a clean 422 here rather than an unhandled error later.
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_must_fit_bcrypt_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes when UTF-8 encoded.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserPublic(BaseModel):
    """Safe response shape for a user — never includes hashed_password."""

    id: uuid.UUID
    email: EmailStr
    username: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
