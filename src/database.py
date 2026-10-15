import logging
from typing import AsyncGenerator, Dict, Any
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base

from src.config import get_settings

logger = logging.getLogger("opsbrain.database")

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.LOG_LEVEL.upper() == "DEBUG"),
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

Base = declarative_base(metadata=metadata)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def check_database_readiness() -> Dict[str, Any]:
    """Check connectivity to PostgreSQL and verify pgvector extension readiness."""
    readiness: Dict[str, Any] = {
        "connected": False,
        "pgvector_ready": False,
        "error": None,
    }

    try:
        async with engine.connect() as conn:
            # 1. Verify connection
            await conn.execute(text("SELECT 1"))
            readiness["connected"] = True

            # 2. Verify pgvector extension
            result = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            )
            extension_row = result.fetchone()
            if extension_row and extension_row[0] == "vector":
                readiness["pgvector_ready"] = True
            else:
                readiness["error"] = "pgvector extension is not activated in database"
                logger.warning(readiness["error"])

    except Exception as exc:
        error_msg = f"Database readiness check failed: {exc}"
        readiness["error"] = str(exc)
        logger.error(error_msg, exc_info=True)

    return readiness
