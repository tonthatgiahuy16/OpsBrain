from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from src.database import engine


@pytest_asyncio.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    yield engine
    await engine.dispose()
