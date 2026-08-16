"""
Shared pytest fixtures for backend tests.

Tests run against a real Postgres database (set via TEST_DATABASE_URL,
defaulting to a `researchpilot_test` DB alongside the dev one) using
Base.metadata.create_all()/drop_all() rather than running Alembic
migrations — faster for a test suite, at the cost of not exercising
the migration files themselves. See Task 2.2 risk assessment.

The engine is created inside a function-scoped fixture rather than at
module level: asyncpg connections are bound to the event loop they
were created in, and pytest-asyncio gives each test function its own
loop by default. A module-level engine would have its connections
opened against the first test's loop, then fail on every subsequent
test with "another operation is in progress" / cross-loop errors.

Deployment milestone: storage_service.py now talks to Cloudflare R2
(S3-compatible) instead of local disk, via aioboto3. Rather than have
every test file that touches document upload/download/process mock
that boundary individually, this file provides one shared, autouse
in-memory fake R2 backend — mirrors this project's established
"construct per call via a small factory function, monkeypatch that
one factory in tests" pattern already used for
embedding_service._get_client() and llm_service._get_client().
No real network call to R2 is ever made by any test.
"""
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from botocore.exceptions import ClientError
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import Base, get_db
from app.main import app

# TEST_DATABASE_URL = os.environ.get(
#     "TEST_DATABASE_URL",
#     "postgresql+asyncpg://postgres:Harsh%402004@localhost:5432/researchpilot_test",
# )

from app.core.config import settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    settings.database_url.replace(
        "/researchpilot",
        "/researchpilot_test",
    ),
)


class _FakeR2Body:
    """Stands in for aioboto3's StreamingBody — an async context
    manager whose .read() returns the object's bytes, matching
    storage_service.py's own `async with response["Body"] as body:`
    usage exactly."""

    def __init__(self, data: bytes):
        self._data = data

    async def read(self) -> bytes:
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeR2Client:
    """An in-memory stand-in for an aioboto3 S3 client, scoped to one
    test via a fresh dict per fixture invocation. Supports exactly the
    three operations storage_service.py calls: put_object,
    get_object, delete_object."""

    def __init__(self, store: dict[str, bytes]):
        self._store = store

    async def put_object(self, *, Bucket, Key, Body, ContentType=None):
        self._store[Key] = Body

    async def get_object(self, *, Bucket, Key):
        if Key not in self._store:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _FakeR2Body(self._store[Key])}

    async def delete_object(self, *, Bucket, Key):
        # Idempotent, matching real S3/R2 delete_object semantics —
        # deleting an already-absent key is not an error.
        self._store.pop(Key, None)


class _FakeR2ClientContext:
    """Matches storage_service._get_client_context()'s return shape —
    callers do `async with _get_client_context() as client:`."""

    def __init__(self, client: _FakeR2Client):
        self._client = client

    async def __aenter__(self) -> _FakeR2Client:
        return self._client

    async def __aexit__(self, *exc_info):
        return False


@pytest.fixture(autouse=True)
def _mock_r2_storage(monkeypatch):
    """
    Autouse for every test in the suite: replaces
    storage_service._get_client_context() with one backed by a fresh,
    empty in-memory dict per test — so any test that uploads,
    processes, downloads, or deletes a document via the real API
    transparently works without needing its own per-file R2 mock, the
    same way no test needs its own OpenAI mock thanks to
    embedding_service/llm_service's identical factory-function
    pattern. No real network call to Cloudflare R2 is ever made.
    """
    from app.services import storage_service

    store: dict[str, bytes] = {}

    def _fake_get_client_context():
        return _FakeR2ClientContext(_FakeR2Client(store))

    monkeypatch.setattr(storage_service, "_get_client_context", _fake_get_client_context)
    yield store


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Fresh engine + schema per test: creates all tables, yields a
    session, drops all tables, then disposes the engine — full
    isolation between tests with no cross-test/cross-loop state.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An httpx client wired to the FastAPI app with the test DB session injected."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
