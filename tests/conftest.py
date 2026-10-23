from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings
from src.database import engine


@pytest_asyncio.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def migration_database_engine() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    migration_engine = create_async_engine(
        settings.MIGRATION_DATABASE_URL,
    )

    yield migration_engine

    await migration_engine.dispose()
