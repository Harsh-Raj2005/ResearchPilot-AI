"""
Security primitives: password hashing and JWT access tokens.

Everything security-sensitive lives here and nowhere else — services
and routers call into this module, they never touch bcrypt or jwt
directly. That keeps the one place that needs careful review small
and auditable.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# bcrypt's algorithm has a hard 72-byte input limit — anything beyond
# that is silently ignored by the underlying C implementation, which
# would let two different long passwords hash identically. We reject
# oversized passwords explicitly instead of allowing that silent
# truncation.
_MAX_PASSWORD_BYTES = 72


class PasswordTooLongError(ValueError):
    """Raised when a plaintext password exceeds bcrypt's 72-byte input limit."""


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password for storage. Never store plain_password itself."""
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > _MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"Password must be at most {_MAX_PASSWORD_BYTES} bytes."
        )
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Check a plaintext password against a stored bcrypt hash.

    Always returns a bool rather than raising on mismatch, and always
    performs the bcrypt comparison (see auth_service.authenticate_user
    for how this is used against a dummy hash when no user is found,
    to avoid a timing side-channel that reveals whether an email is
    registered).
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        # Malformed hash (shouldn't happen for hashes we generated ourselves).
        return False


def create_access_token(subject: str | uuid.UUID, expires_delta: timedelta | None = None) -> str:
    """
    Create a signed JWT access token.

    `subject` is the token's `sub` claim — the user's id. Callers pass
    a UUID or a string; it's stored as a string since JWT claims are
    JSON-serializable text.
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT access token.

    Raises `jwt.PyJWTError` (or a subclass, e.g. `jwt.ExpiredSignatureError`)
    on an invalid, tampered, or expired token — callers are expected to
    handle that, not this module. Not yet wired into any route/dependency;
    that's Task 2.3.
    """
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
