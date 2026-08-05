"""
Tests for app.core.deps.get_current_user.

No protected route exists yet (that's Task 3B), so this dependency is
called directly rather than through an HTTP client — the same
approach used for app.core.security in Task 2.2's unit tests.
"""
import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.security import create_access_token
from app.services import auth_service


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_get_current_user_valid_token_returns_user(db_session: AsyncSession):
    user = await auth_service.create_user(
        db_session, email="deps@example.com", username="depsuser", password="password123"
    )
    token = create_access_token(subject=user.id)

    result = await get_current_user(credentials=_bearer(token), db=db_session)

    assert result.id == user.id
    assert result.email == "deps@example.com"


async def test_get_current_user_rejects_tampered_token(db_session: AsyncSession):
    user = await auth_service.create_user(
        db_session, email="tampered@example.com", username="tampereduser", password="password123"
    )
    token = create_access_token(subject=user.id)
    mid = len(token) // 2
    tampered = token[:mid] + ("A" if token[mid] != "A" else "B") + token[mid + 1 :]

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_bearer(tampered), db=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_user_rejects_token_for_nonexistent_user(db_session: AsyncSession):
    token = create_access_token(subject=uuid.uuid4())  # valid signature, no matching user

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_bearer(token), db=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_user_rejects_inactive_user(db_session: AsyncSession):
    user = await auth_service.create_user(
        db_session, email="inactive@example.com", username="inactiveuser", password="password123"
    )
    user.is_active = False
    await db_session.commit()
    token = create_access_token(subject=user.id)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_bearer(token), db=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_user_rejects_malformed_subject_claim(db_session: AsyncSession):
    token = create_access_token(subject="not-a-uuid")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_bearer(token), db=db_session)
    assert exc_info.value.status_code == 401
