"""
Auth tests.

Covers the two endpoints end-to-end (via the HTTP client fixture) plus
the security utilities directly, since they're the highest-risk code
in this task and deserve their own unit-level coverage independent of
the HTTP layer.
"""
import jwt
import pytest
from httpx import AsyncClient

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


# --- security.py unit tests ---


def test_hash_password_produces_verifiable_hash():
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("wrong-password", hashed) is False


def test_create_and_decode_access_token_roundtrip():
    token = create_access_token(subject="some-user-id")
    payload = decode_access_token(token)
    assert payload["sub"] == "some-user-id"


def test_decode_access_token_rejects_tampered_token():
    token = create_access_token(subject="some-user-id")
    # Flip a character in the middle of the payload segment, not the
    # last character of the whole token — base64url's last character
    # can carry padding bits that don't always change the decoded
    # bytes, which made this test flaky when it tampered with the tail.
    mid = len(token) // 2
    flipped_char = "A" if token[mid] != "A" else "B"
    tampered = token[:mid] + flipped_char + token[mid + 1 :]
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(tampered)


# --- signup endpoint ---


async def test_signup_success(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": "new@example.com", "username": "newuser", "password": "password123"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["username"] == "newuser"
    assert "hashed_password" not in body
    assert "password" not in body


async def test_signup_duplicate_email_rejected(client: AsyncClient):
    payload = {"email": "dup@example.com", "username": "userone", "password": "password123"}
    first = await client.post("/api/v1/auth/signup", json=payload)
    assert first.status_code == 201

    payload["username"] = "usertwo"
    second = await client.post("/api/v1/auth/signup", json=payload)
    assert second.status_code == 409


async def test_signup_duplicate_username_rejected(client: AsyncClient):
    payload = {"email": "userone@example.com", "username": "dupname", "password": "password123"}
    first = await client.post("/api/v1/auth/signup", json=payload)
    assert first.status_code == 201

    payload["email"] = "usertwo@example.com"
    second = await client.post("/api/v1/auth/signup", json=payload)
    assert second.status_code == 409


async def test_signup_rejects_short_password(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": "short@example.com", "username": "shortpw", "password": "short"},
    )
    assert response.status_code == 422


async def test_signup_rejects_password_exceeding_bcrypt_byte_limit(client: AsyncClient):
    # 72 characters but 144 bytes (each 'é' is 2 bytes in UTF-8) — under
    # the schema's max_length=72 char count, but over bcrypt's byte limit.
    # Must fail as a clean 422, not an unhandled error.
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": "multibyte@example.com", "username": "multibyte", "password": "é" * 72},
    )
    assert response.status_code == 422


# --- login endpoint ---


async def test_login_success(client: AsyncClient):
    await client.post(
        "/api/v1/auth/signup",
        json={"email": "login@example.com", "username": "loginuser", "password": "password123"},
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "login@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    decoded = decode_access_token(body["access_token"])
    assert "sub" in decoded


async def test_login_wrong_password_rejected(client: AsyncClient):
    await client.post(
        "/api/v1/auth/signup",
        json={"email": "wrongpw@example.com", "username": "wrongpwuser", "password": "password123"},
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "wrongpw@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 401


async def test_login_nonexistent_user_rejected(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever123"},
    )
    assert response.status_code == 401
