"""
FastAPI dependencies.

get_current_user is the project's first protected-route dependency:
extracts a bearer token, verifies it via app.core.security's already-
tested decode_access_token(), loads the corresponding User, and
rejects (401) on any failure — missing token, invalid/tampered/expired
token, user not found, or an inactive user. All failure cases return
the same error, matching the auth_service pattern from Task 2.2 (never
reveal which part of the credential chain failed).

Not yet consumed by any route — this file has no observable HTTP
behavior on its own. Task 3B wires it into the documents API.
"""
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

# HTTPBearer, not OAuth2PasswordBearer: our /auth/login accepts a JSON
# body, not the OAuth2 password-flow's form-encoded username/password.
# OAuth2PasswordBearer would misrepresent that contract in the
# generated Swagger docs. HTTPBearer accurately reflects a plain
# bearer-token scheme — Swagger's "Authorize" dialog just needs a
# pasted token, no form fields implying a different login contract.
bearer_scheme = HTTPBearer()


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        # PyJWTError: invalid signature, tampered, or expired token.
        # KeyError: token decoded fine but has no "sub" claim.
        # ValueError: "sub" claim isn't a valid UUID.
        raise _credentials_error()

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise _credentials_error()

    return user
