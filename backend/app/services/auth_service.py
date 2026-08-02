"""
Auth business logic.

Routers call these functions and translate their results/exceptions
into HTTP responses; no HTTP or request/response concerns belong here.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User

# A precomputed hash of a value nobody will ever type, used to run
# verify_password() even when no matching user exists. This keeps
# authenticate_user()'s runtime roughly constant whether or not the
# email is registered, so response timing doesn't leak that information.
_DUMMY_HASH = hash_password("not-a-real-password-used-only-for-timing")


class EmailAlreadyExistsError(Exception):
    """Raised when signup is attempted with an email already in use."""


class UsernameAlreadyExistsError(Exception):
    """Raised when signup is attempted with a username already in use."""


class InvalidCredentialsError(Exception):
    """Raised when login credentials don't match any active user."""


async def create_user(db: AsyncSession, *, email: str, username: str, password: str) -> User:
    """
    Create a new user after checking email/username uniqueness.

    Raises EmailAlreadyExistsError / UsernameAlreadyExistsError rather
    than relying solely on the DB's unique constraint, so the router
    can return a precise, friendly error instead of a generic 500 from
    an IntegrityError.
    """
    existing_email = await db.execute(select(User).where(User.email == email))
    if existing_email.scalar_one_or_none() is not None:
        raise EmailAlreadyExistsError(email)

    existing_username = await db.execute(select(User).where(User.username == username))
    if existing_username.scalar_one_or_none() is not None:
        raise UsernameAlreadyExistsError(username)

    user = User(email=email, username=username, hashed_password=hash_password(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, *, email: str, password: str) -> User:
    """
    Verify credentials and return the matching active user.

    Raises InvalidCredentialsError for any failure case (no such user,
    wrong password, inactive user) — deliberately the same error for
    all three, so a login failure never reveals which part was wrong.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        verify_password(password, _DUMMY_HASH)  # constant-time-ish: see module docstring
        raise InvalidCredentialsError()

    if not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()

    if not user.is_active:
        raise InvalidCredentialsError()

    return user
