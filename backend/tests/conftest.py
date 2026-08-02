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
"""
import os
from collections.abc import AsyncGenerator

import pytest_asyncio
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
